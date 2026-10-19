# SPDX-License-Identifier: Apache-2.0
"""Validate operation bindings and schema pointers, without claiming task semantics."""

import json
import re

from .runtime import bind
from .source import parameters, request_schema, resolve, response_schema


def nodes_at(spec, schema, path, seen=()):
    """Walk a response/request JSON pointer through OpenAPI refs and unions."""
    if not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    marker = (ref, path)
    if ref and marker in seen:
        return []
    schema = resolve(spec, schema)
    seen = seen + (marker,)
    if not path:
        return [schema]
    token, *rest = path
    remaining = rest
    nodes = []
    for union in ("oneOf", "anyOf", "allOf"):
        for branch in schema.get(union, []):
            nodes.extend(nodes_at(spec, branch, path, seen))
    if token in schema.get("properties", {}):
        nodes.extend(nodes_at(spec, schema["properties"][token], remaining, seen))
    if (token == "*" or token.isdigit()) and "items" in schema:
        items = schema["items"]
        for item in items if isinstance(items, list) else [items]:
            nodes.extend(nodes_at(spec, item, remaining, seen))
    return nodes


def split_pointer(path):
    if path == "":
        return []
    if not path.startswith("/"):
        raise ValueError("Not a JSON pointer")
    if re.search(r"~(?![01])", path):
        raise ValueError("Invalid JSON pointer escape")
    return [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]


def validate_body(spec, schema, value, prefix=""):
    issues = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            nodes = nodes_at(spec, schema, split_pointer(path))
            if not nodes:
                issues.append("Undocumented request field " + path)
            issues += validate_body(spec, schema, child, path)
    elif isinstance(value, list):
        for item in value:
            issues += validate_body(spec, schema, item, prefix + "/*")
    return issues


def audit_request(index, request, task, available_documents):
    issues, warnings = [], []
    key = request["operation"]
    if key not in index.by_key:
        return {
            "id": request["id"],
            "operation": key,
            "issues": ["Operation does not exist in official specification"],
            "warnings": [],
        }
    entry = index.by_key[key]
    operation = entry["operation"]
    if task["provider"] + "::" + key not in available_documents:
        warnings.append("Operation exists but its excerpt was not retrieved for the model")
    try:
        binding = bind(json.loads(request["bindings_json"]), task["context"])
        if not isinstance(binding, dict) or set(binding) - {"path", "query", "body", "header"}:
            raise ValueError("Bindings must use path/query/body/header objects")
        docs = parameters(index.spec, entry["item"], operation)
        documented = {(p.get("in"), p["name"]): p for p in docs}
        for location in ("path", "query", "header"):
            supplied = binding.get(location, {})
            if not isinstance(supplied, dict):
                raise ValueError("Binding location must be an object")
            for name in supplied:
                if (location, name) not in documented:
                    # Standard auth/version/content headers aren't operation parameters.
                    if location == "header" and name.lower() in {
                        "accept",
                        "authorization",
                        "content-type",
                        "stripe-version",
                        "x-github-api-version",
                    }:
                        continue
                    issues.append(f"Undocumented {location} parameter {name}")
        for p in docs:
            if (
                p.get("required")
                and p.get("in") in ("path", "query")
                and p["name"] not in binding.get(p["in"], {})
            ):
                # Swagger's required token is auth infrastructure, not a literal planner secret.
                if p["name"] != "token":
                    issues.append(f"Missing required {p['in']} parameter {p['name']}")
        expected_path_names = set(re.findall(r"\{([^}]+)\}", entry["path"]))
        if not expected_path_names <= binding.get("path", {}).keys():
            issues.append("Not every URL path placeholder is bound")
        if "body" in binding:
            body_schema = request_schema(index.spec, operation)
            if body_schema:
                issues += validate_body(index.spec, body_schema, binding["body"])
                for required in resolve(index.spec, body_schema).get("required", []):
                    if required not in binding["body"]:
                        issues.append("Missing required request body field " + required)
            else:
                for key in binding["body"]:
                    if ("formData", key) not in documented and not any(
                        p.get("in") == "body" for p in docs
                    ):
                        issues.append("Undocumented body/form parameter " + key)
        if entry["method"] != "get":
            warnings.append(
                "Non-GET proposed observation: semantic read-only review required; never executed by this lab"
            )
        if not request["auth_notes"]:
            warnings.append("No authentication/access notes supplied")
    except (KeyError, TypeError, ValueError) as exc:
        issues.append("Invalid binding: " + str(exc))
        binding = None
    return {
        "id": request["id"],
        "operation": request["operation"],
        "issues": issues,
        "warnings": warnings,
        "resolved_bindings": binding,
    }


def audit_check(index, check, request):
    issues = []
    if request is None or request["operation"] not in index.by_key:
        return {"id": check["id"], "issues": ["Check references missing/unknown operation"]}
    entry = index.by_key[request["operation"]]
    schema = response_schema(index.spec, entry["operation"])
    try:
        collection_path = split_pointer(check["collection_pointer"])
        collection_nodes = nodes_at(index.spec, schema, collection_path)
        if not collection_nodes:
            issues.append("Undocumented response collection " + check["collection_pointer"])
        is_array = any("items" in node or node.get("type") == "array" for node in collection_nodes)
        base = collection_path + (["*"] if is_array else [])
        candidates = {"field": check["field_pointer"]}
        selector = json.loads(check["selector_json"])
        if not isinstance(selector, dict):
            raise ValueError("Selector must be an object")
        candidates.update({"selector " + key: key for key in selector})
        if check["select_mode"] == "latest":
            candidates["order_by"] = check["order_by"]
            if not check["order_by"]:
                issues.append("Latest selection requires an order_by field")
        for label, path in candidates.items():
            if not nodes_at(index.spec, schema, base + split_pointer(path)):
                issues.append("Undocumented response " + label + " " + path)
        json.loads(check["value_json"])
    except (KeyError, TypeError, ValueError) as exc:
        issues.append("Invalid check expression: " + str(exc))
    return {"id": check["id"], "issues": issues}


def audit_pagination(index, request):
    issues = []
    entry = index.by_key.get(request["operation"])
    if entry is None:
        return issues
    pagination = request["pagination"]
    if pagination["mode"] == "none":
        if any(
            pagination[key]
            for key in (
                "items_pointer",
                "cursor_parameter",
                "next_cursor_pointer",
                "has_more_pointer",
                "cursor_item_pointer",
            )
        ):
            issues.append("Single-response pagination must not declare continuation fields")
        return issues
    schema = response_schema(index.spec, entry["operation"])
    nodes = nodes_at(index.spec, schema, split_pointer(pagination["items_pointer"]))
    if not any(node.get("type") == "array" or "items" in node for node in nodes):
        issues.append("Pagination items must be a documented array")
    if pagination["mode"] == "cursor":
        docs = parameters(index.spec, entry["item"], entry["operation"])
        if not any(
            parameter.get("in") == "query"
            and parameter.get("name") == pagination["cursor_parameter"]
            for parameter in docs
        ):
            issues.append("Pagination cursor parameter is undocumented")
        if not pagination["next_cursor_pointer"] and not pagination["has_more_pointer"]:
            issues.append("Cursor pagination needs explicit completion evidence")
    return issues
