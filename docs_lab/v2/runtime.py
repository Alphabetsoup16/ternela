# SPDX-License-Identifier: Apache-2.0
"""Execute bounded pagination through an injected transport, then evaluate evidence."""

import copy
import json
import re
from urllib.parse import urljoin, urlsplit

from docs_lab.runtime import bind, compare
from docs_lab.source import pointer


def replace_at(body, path, value):
    if not path:
        return value
    result = copy.deepcopy(body)
    parts = path[1:].split("/")
    parent = pointer(result, "/" + "/".join(parts[:-1])) if len(parts) > 1 else result
    key = parts[-1].replace("~1", "/").replace("~0", "~")
    parent[int(key) if isinstance(parent, list) else key] = value
    return result


def next_links(header):
    """Accept standard Link syntax; malformed metadata cannot establish an end."""
    if not isinstance(header, str):
        raise ValueError("Invalid Link header")
    pattern = re.compile(r'\s*<([^>]+)>\s*((?:;\s*[\w-]+=(?:"[^"]*"|[^,;\s]+)\s*)*)(?:,|$)')
    links, position = [], 0
    while header[position:].strip():
        match = pattern.match(header, position)
        if match is None:
            raise ValueError("Malformed Link header")
        parameters = re.findall(r';\s*([\w-]+)=("[^"]*"|[^,;\s]+)', match[2])
        relations = [value.strip('"').split() for key, value in parameters if key.lower() == "rel"]
        if any("next" in values for values in relations):
            links.append(match[1])
        position = match.end()
    return links


def bind_observations(value, context, observed):
    if isinstance(value, str) and value.startswith("$response."):
        identity, separator, path = value[len("$response.") :].partition("/")
        envelope = observed[identity]
        if envelope.get("collection_complete") is not True:
            raise ValueError("Dependency observation is incomplete")
        return pointer(envelope["body"], "/" + path if separator else "")
    if isinstance(value, dict):
        return {k: bind_observations(v, context, observed) for k, v in value.items()}
    if isinstance(value, list):
        return [bind_observations(v, context, observed) for v in value]
    return bind(value, context)


def execute(request, context, transport, observed=None):
    pagination = request["pagination"]
    binding = bind_observations(json.loads(request["bindings_json"]), context, observed or {})
    current = {"operation": request["operation"], "bindings": binding, "next_url": None}
    rows, trace, seen = [], [], set()
    first, last = None, None
    complete, reason = False, "Page budget exhausted"
    for number in range(pagination["max_pages"]):
        identity = json.dumps(current, sort_keys=True)
        if identity in seen:
            reason = "Repeated continuation request"
            break
        seen.add(identity)
        try:
            envelope = transport(copy.deepcopy(current))
            if not isinstance(envelope, dict):
                raise ValueError("Malformed response")
            last = envelope
            trace.append(
                {
                    "page": number + 1,
                    "request": copy.deepcopy(current),
                    "http_status": envelope.get("http_status"),
                }
            )
            if (
                type(envelope.get("http_status")) is not int
                or not 200 <= envelope["http_status"] < 300
            ):
                reason = "HTTP error or missing response"
                break
            body = envelope.get("body")
            if isinstance(body, dict) and body.get("ok") is False:
                reason = "Provider error"
                break
            if first is None:
                first = copy.deepcopy(envelope)
            if pagination["mode"] == "none":
                complete = not isinstance(body, list)
                reason = (
                    "Single object response"
                    if complete
                    else "Root list requires explicit pagination"
                )
                break
            items = pointer(body, pagination["items_pointer"])
            if not isinstance(items, list):
                raise ValueError("Pagination items are not an array")
            rows.extend(items)
            if pagination["mode"] == "link":
                if envelope.get("headers_complete") is not True:
                    raise ValueError("Response headers not completely captured")
                raw_headers = envelope.get("headers", {})
                if not isinstance(raw_headers, dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in raw_headers.items()
                ):
                    raise ValueError("Malformed response headers")
                headers = {k.lower(): v for k, v in raw_headers.items()}
                links = next_links(headers.get("link", ""))
                if not links:
                    complete, reason = True, "No next link in complete headers"
                    break
                if len(links) != 1 or not envelope.get("url"):
                    raise ValueError("Ambiguous next link or missing origin URL")
                url = urljoin(envelope["url"], links[0])
                old, new = urlsplit(first["url"]), urlsplit(url)
                if (new.scheme, new.netloc, new.path) != (
                    old.scheme,
                    old.netloc,
                    old.path,
                ) or new.fragment:
                    raise ValueError("Continuation changes origin or resource")
                current["next_url"] = url
            else:
                more_path = pagination["has_more_pointer"]
                more = pointer(body, more_path) if more_path else None
                if more_path and type(more) is not bool:
                    raise ValueError("Invalid has-more flag")
                if more is False:
                    if pagination["next_cursor_pointer"] and pointer(
                        body, pagination["next_cursor_pointer"]
                    ) not in ("", None):
                        raise ValueError("Conflicting pagination signals")
                    complete, reason = True, "Provider has-more flag is false"
                    break
                token_path = pagination["next_cursor_pointer"]
                if token_path:
                    token = pointer(body, token_path)
                    if token in ("", None) and more is not True:
                        complete, reason = True, "Provider cursor exhausted"
                        break
                else:
                    if more is not True or not items or not pagination["cursor_item_pointer"]:
                        raise ValueError("Missing cursor continuation evidence")
                    token = pointer(items[-1], pagination["cursor_item_pointer"])
                if token in (None, "") or not isinstance(token, (str, int)) or type(token) is bool:
                    raise ValueError("Missing or malformed next cursor")
                parameter = pagination["cursor_parameter"]
                if not parameter:
                    raise ValueError("Missing cursor parameter")
                current["bindings"].setdefault("query", {})[parameter] = token
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            reason = str(exc)
            break
    result = (
        copy.deepcopy(last if complete else first)
        if (last if complete else first)
        else {"http_status": (last or {}).get("http_status", 0), "body": None}
    )
    if first and pagination["mode"] != "none":
        result["body"] = replace_at(result["body"], pagination["items_pointer"], rows)
    result.update(
        collection_complete=complete,
        completed_pointer=pagination["items_pointer"],
        traversal_reason=reason,
        trace=trace,
    )
    return result


def select_check(check, envelope, context):
    """Return the rows a check actually evaluates, including latest-record ties."""
    if (
        not isinstance(envelope, dict)
        or type(envelope.get("http_status")) is not int
        or not 200 <= envelope["http_status"] < 300
    ):
        raise ValueError("Observation is unavailable")
    body = envelope.get("body")
    if isinstance(body, dict) and body.get("ok") is False:
        raise ValueError("Provider reported an error")
    if envelope.get("collection_complete") is not True:
        raise ValueError("Observation is incomplete")
    collection = pointer(body, check["collection_pointer"])
    if (
        isinstance(collection, list)
        and envelope.get("completed_pointer") != check["collection_pointer"]
    ):
        raise ValueError("Check selects a different collection")
    rows = collection if isinstance(collection, list) else [collection]
    selector = bind(json.loads(check["selector_json"]), context)
    if not isinstance(selector, dict):
        raise ValueError("Selector must be an object")
    selected = [
        row
        for row in rows
        if all(compare(pointer(row, key), "eq", value) for key, value in selector.items())
    ]
    if check["select_mode"] == "latest" and selected:
        values = [order_key(pointer(row, check["order_by"])) for row in selected]
        if len({value[0] for value in values}) != 1:
            raise ValueError("Latest ordering mixes incompatible types")
        newest = max(values)
        selected = [row for row, value in zip(selected, values) if value == newest]
    return selected


def order_key(value):
    """Order finite numbers, timezone-aware ISO timestamps, or ordinary strings."""
    import math
    from datetime import datetime

    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ValueError("Latest ordering requires finite evidence")
        return "number", value
    if isinstance(value, str):
        if re.match(r"^\d{4}-\d\d-\d\dT", value):
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                raise ValueError("Latest timestamp has no timezone")
            return "timestamp", moment.timestamp()
        return "string", value
    raise ValueError("Latest ordering requires a number or string")


def check_details(check, envelope, context):
    """Textual JSON preserves exact integers in the browser's evidence table."""
    details = {
        "expected_json": json.dumps(
            bind(json.loads(check["value_json"]), context), ensure_ascii=False
        ),
        "actual_json": None,
    }
    try:
        selected = select_check(check, envelope, context)
        if check["select_mode"] == "count":
            actual = len(selected)
        else:
            values = [pointer(row, check["field_pointer"]) for row in selected]
            actual = values[0] if len(values) == 1 else values
        details["actual_json"] = json.dumps(actual, ensure_ascii=False, allow_nan=False)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError):
        pass
    return details


def check_one(check, envelope, context):
    try:
        selected = select_check(check, envelope, context)
        expected = bind(json.loads(check["value_json"]), context)
        mode = check["select_mode"]
        if mode == "count":
            return "true" if compare(len(selected), check["op"], expected) else "false"
        if not selected:
            if mode == "latest":
                return "unknown"
            if check["op"] == "exists":
                return "true" if expected is False else "false"
            return "false"
        if mode == "single" and len(selected) != 1:
            return "unknown"
        outcomes = []
        for row in selected:
            value = pointer(row, check["field_pointer"])
            outcomes.append(
                compare(value is not None, "eq", expected)
                if check["op"] == "exists"
                else compare(value, check["op"], expected)
            )
        if mode == "latest" and len(set(outcomes)) > 1:
            return "unknown"
        return "true" if (any(outcomes) if mode == "any" else all(outcomes)) else "false"
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError):
        return "unknown"


def evaluate(plan, responses, context, phase="after"):
    checks = [
        {"id": c["id"], "outcome": check_one(c, responses.get(c["request_id"]), context)}
        for c in plan["checks"]
        if c["phase"] == phase
    ]
    gaps = [
        r for r in plan["requirements"] if r["phase"] == phase and r["coverage"] != "observable"
    ]
    status = (
        "unverifiable"
        if gaps
        else (
            "unknown"
            if not checks or any(c["outcome"] == "unknown" for c in checks)
            else (
                ("complete" if phase == "after" else "ready")
                if all(c["outcome"] == "true" for c in checks)
                else "pending"
                if phase == "after"
                else "blocked"
            )
        )
    )
    return {"status": status, "checks": checks, "gaps": gaps}
