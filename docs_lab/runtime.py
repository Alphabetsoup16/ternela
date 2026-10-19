# SPDX-License-Identifier: Apache-2.0
"""Small data-only interpreter for checks proposed from API documentation."""

import json
import operator

from .source import pointer

OPS = {"eq": operator.eq, "ne": operator.ne, "lt": operator.lt,
       "lte": operator.le, "gt": operator.gt, "gte": operator.ge}


def bind(value, context):
    if isinstance(value, str) and value.startswith("$context."):
        return pointer(context, "/" + value[len("$context."):].replace(".", "/"))
    if isinstance(value, dict):
        return {k: bind(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [bind(v, context) for v in value]
    return value


def compare(left, op, right):
    if op in ("eq", "ne"):
        # Prevent True == 1 from accidentally satisfying a typed condition.
        same_type = type(left) is type(right) or (type(left) in (int, float) and type(right) in (int, float))
        equal = same_type and left == right
        return equal if op == "eq" else not equal
    if type(left) not in (int, float) or type(right) not in (int, float):
        raise ValueError("Ordering requires numeric evidence")
    return OPS[op](left, right)


def check_one(check, envelope, context):
    if not isinstance(envelope, dict) or type(envelope.get("http_status")) is not int or not 200 <= envelope["http_status"] < 300:
        return "unknown"
    body = envelope.get("body")
    if isinstance(body, dict) and body.get("ok") is False:
        return "unknown"
    try:
        collection = pointer(body, check["collection_pointer"])
        rows = collection if isinstance(collection, list) else [collection]
        selector = bind(json.loads(check["selector_json"]), context)
        if not isinstance(selector, dict):
            return "unknown"
        selected = []
        for row in rows:
            if all(compare(pointer(row, key), "eq", value) for key, value in selector.items()):
                selected.append(row)
        # Empty/paginated evidence cannot automatically establish universal absence.
        if not selected:
            return "unknown"
        if check["select_mode"] == "latest":
            if not check["order_by"]:
                return "unknown"
            newest = max(pointer(row, check["order_by"]) for row in selected)
            selected = [row for row in selected if pointer(row, check["order_by"]) == newest]
        elif check["select_mode"] == "single" and len(selected) != 1:
            return "unknown"
        expected = bind(json.loads(check["value_json"]), context)
        outcomes = []
        for row in selected:
            value = pointer(row, check["field_pointer"])
            outcomes.append(value is not None if check["op"] == "exists" else compare(value, check["op"], expected))
        if check["select_mode"] == "latest" and len(set(outcomes)) > 1:
            return "unknown"
        okay = any(outcomes) if check["select_mode"] == "any" else all(outcomes)
        return "true" if okay else "false"
    except (KeyError, IndexError, TypeError, ValueError):
        return "unknown"


def evaluate(plan, responses, context, phase="after"):
    selected = [c for c in plan["checks"] if c["phase"] == phase]
    checks = [{"id": c["id"], "outcome": check_one(c, responses.get(c["request_id"]), context)} for c in selected]
    gaps = [r for r in plan["requirements"] if r["phase"] == phase and r["coverage"] != "observable"]
    if gaps:
        status = "unverifiable"
    elif not selected or any(c["outcome"] == "unknown" for c in checks):
        status = "unknown"
    elif all(c["outcome"] == "true" for c in checks):
        status = "complete" if phase == "after" else "ready"
    else:
        status = "pending" if phase == "after" else "blocked"
    return {"status": status, "checks": checks, "gaps": gaps}
