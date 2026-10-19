# SPDX-License-Identifier: Apache-2.0
"""Complete operation directory and navigable schema trees; no tail-field deletion."""

from docs_lab.audit import nodes_at, split_pointer
from docs_lab.source import parameters, request_schema, resolve, response_schema


def tree(spec, schema, path="", depth=0, max_depth=3, seen=()):
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    node = resolve(spec, schema)
    result = {k: node[k] for k in ("type", "enum", "format", "required", "nullable") if k in node}
    if ref:
        result["$ref"] = ref
    if node.get("description"):
        result["description"] = node["description"][:240]
    result["expand_at"] = path
    if ref and ref in seen:
        result["remaining"] = "recursive reference"
        return result
    ancestry = seen + ((ref,) if ref else ())
    boundary = depth >= max_depth
    # Keep every child name, even at the depth boundary. Never keep only a prefix
    # of the field list as v1 did. The next read can target an inline schema path.
    if "properties" in node:
        result["properties"] = {}
        for name, child in node["properties"].items():
            child_path = path + "/" + name.replace("~", "~0").replace("/", "~1")
            result["properties"][name] = ({"expand_at": child_path, "type": resolve(spec, child).get("type"), "remaining": True}
                                           if boundary else tree(spec, child, child_path, depth + 1, max_depth, ancestry))
    if "items" in node:
        items = node["items"]
        array = items if isinstance(items, list) else [items]
        result["items"] = [{"expand_at": path + "/*", "remaining": True} if boundary else
                           tree(spec, item, path + "/*", depth + 1, max_depth, ancestry) for item in array]
    for union in ("oneOf", "anyOf", "allOf"):
        if union in node:
            result[union] = [tree(spec, branch, path, depth, max_depth, ancestry) for branch in node[union]]
    return result


def directory(index):
    return [entry["key"] + "\t" + entry["operation"].get("summary", "")[:75] for entry in index.entries]


def root_names(index, operation):
    entry = index.by_key[operation]
    op = entry["operation"]
    def names(schema):
        node = resolve(index.spec, schema)
        return {"properties": list(node.get("properties", {})), "type": node.get("type"), "$ref": schema.get("$ref")}
    return {"operation": operation, "request_root": names(request_schema(index.spec, op)),
            "response_root": names(response_schema(index.spec, op))}


def card(index, operation, depth=1):
    entry = index.by_key[operation]
    op = entry["operation"]
    return {"operation": operation, "description": op.get("description", "")[:4000],
            "parameters": parameters(index.spec, entry["item"], op),
            "security": op.get("security", index.spec.get("security", [])),
            "request_schema": tree(index.spec, request_schema(index.spec, op), max_depth=depth),
            "response_schema": tree(index.spec, response_schema(index.spec, op), max_depth=depth),
            "projection": "Every child name is retained; expand_at pointers can read inline branches. Descriptions capped at 240 characters; recursion/depth limits are explicit."}


def branch(index, operation, section, path):
    entry = index.by_key[operation]
    schema = (request_schema if section == "request" else response_schema)(index.spec, entry["operation"])
    nodes = nodes_at(index.spec, schema, split_pointer(path))
    # A root read is a directory of branches, not permission to recursively
    # expand every linked customer/charge/subscription object into the prompt.
    depth = 1 if not path else 3
    rendered = [tree(index.spec, n, path=path, max_depth=depth) for n in nodes]
    import json
    while len(json.dumps(rendered)) > 60000 and depth > 0:
        depth -= 1
        rendered = [tree(index.spec, n, path=path, max_depth=depth) for n in nodes]
    return {"operation": operation, "section": section, "pointer": path, "projection_depth": depth,
            "nodes": rendered,
            "found": bool(nodes)}
