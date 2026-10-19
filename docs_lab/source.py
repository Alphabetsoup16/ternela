# SPDX-License-Identifier: Apache-2.0
"""OpenAPI pointers, schema projection and lexical operation retrieval."""

import math
import re
from collections import Counter

METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def pointer(document, path):
    if path in ("", "#"):
        return document
    if path.startswith("#"):
        path = path[1:]
    if not path.startswith("/"):
        raise KeyError(path)
    for part in path[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise KeyError("Invalid JSON pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(document, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", part):
                raise KeyError("Invalid array index")
            document = document[int(part)]
        else:
            document = document[part]
    return document


def resolve(spec, node, seen=()):
    if not isinstance(node, dict) or "$ref" not in node:
        return node
    ref = node["$ref"]
    if not ref.startswith("#/") or ref in seen:
        return node
    target = resolve(spec, pointer(spec, ref), seen + (ref,))
    return dict(target, **{k: v for k, v in node.items() if k != "$ref"})


def response_schema(spec, operation):
    for status, response in operation.get("responses", {}).items():
        if str(status).startswith("2"):
            response = resolve(spec, response)
            if "schema" in response:
                return response["schema"]
            for content_type, body in response.get("content", {}).items():
                if "json" in content_type:
                    return body.get("schema", {})
    return {}


def fields(spec, schema, path="", depth=0, max_depth=4, ancestors=()):
    """Generic loss-marked schema projection, retaining vendor field descriptions."""
    if depth > max_depth or not isinstance(schema, dict):
        return []
    ref = schema.get("$ref")
    if ref and ref in ancestors:
        return [{"pointer": path, "$ref": ref, "truncated": "recursive reference"}]
    node = resolve(spec, schema)
    entry = {"pointer": path}
    for key in (
        "type",
        "description",
        "enum",
        "format",
        "required",
        "nullable",
        "additionalProperties",
    ):
        if key in node:
            val = node[key]
            entry[key] = val[:800] if key == "description" else val
    if ref:
        entry["$ref"] = ref
    rows = [entry]
    next_ancestors = ancestors + ((ref,) if ref else ())
    if depth == max_depth and any(
        k in node for k in ("properties", "items", "allOf", "oneOf", "anyOf")
    ):
        entry["truncated"] = "depth budget; request schema reference for more"
        return rows
    for key in ("allOf", "oneOf", "anyOf"):
        for branch in node.get(key, []):
            rows.extend(fields(spec, branch, path, depth + 1, max_depth, next_ancestors))
    for name, child in node.get("properties", {}).items():
        rows.extend(
            fields(
                spec,
                child,
                path + "/" + name.replace("~", "~0").replace("/", "~1"),
                depth + 1,
                max_depth,
                next_ancestors,
            )
        )
    if "items" in node:
        items = node["items"]
        for item in items if isinstance(items, list) else [items]:
            rows.extend(fields(spec, item, path + "/*", depth + 1, max_depth, next_ancestors))
    return rows


def parameters(spec, path_item, operation):
    return [
        resolve(spec, p) for p in path_item.get("parameters", []) + operation.get("parameters", [])
    ]


def request_schema(spec, operation):
    body = resolve(spec, operation.get("requestBody", {}))
    for content_type, entry in body.get("content", {}).items():
        if "json" in content_type or "form" in content_type:
            return entry.get("schema", {})
    return {}


def tokenize(text):
    # No provider/task synonyms or manually chosen endpoints.
    words = re.findall(r"[a-z][a-z0-9_]*", re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower())
    stop = {
        "the",
        "a",
        "an",
        "to",
        "of",
        "and",
        "in",
        "for",
        "is",
        "it",
        "that",
        "this",
        "be",
        "only",
        "with",
        "on",
        "or",
        "at",
        "as",
    }
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in stop]


class Index:
    def __init__(self, provider, spec):
        self.provider = provider
        self.spec = spec
        self.entries = []
        for path, item in self.spec["paths"].items():
            for method, operation in item.items():
                if method not in METHODS:
                    continue
                # Automatic text extraction from original operations, without flattened response bodies.
                text = " ".join(
                    [
                        method,
                        path,
                        operation.get("operationId", ""),
                        operation.get("summary", ""),
                        operation.get("description", ""),
                        " ".join(operation.get("tags", [])),
                        " ".join(
                            p.get("name", "") + " " + p.get("description", "")
                            for p in parameters(self.spec, item, operation)
                        ),
                    ]
                )
                tokens = Counter(tokenize(text))
                self.entries.append(
                    {
                        "key": method.upper() + " " + path,
                        "method": method,
                        "path": path,
                        "operation": operation,
                        "item": item,
                        "tokens": tokens,
                        "length": sum(tokens.values()),
                    }
                )
        self.by_key = {e["key"]: e for e in self.entries}
        self.df = Counter(w for e in self.entries for w in e["tokens"])
        self.mean_length = sum(e["length"] for e in self.entries) / len(self.entries)

    def search(self, query, top_k=5):
        words = set(tokenize(query))
        results = []
        for entry in self.entries:
            score = 0
            for word in words:
                count = entry["tokens"].get(word, 0)
                if count:
                    idf = math.log(
                        1 + (len(self.entries) - self.df[word] + 0.5) / (self.df[word] + 0.5)
                    )
                    score += (
                        idf
                        * count
                        * 2.2
                        / (count + 1.2 * (0.25 + 0.75 * entry["length"] / self.mean_length))
                    )
            results.append((score, entry["key"]))
        return [
            {"score": round(score, 5), "operation": key}
            for score, key in sorted(results, reverse=True)[:top_k]
        ]

    def excerpt(self, key):
        entry = self.by_key[key]
        operation = entry["operation"]
        result = {
            "operation": key,
            "operationId": operation.get("operationId"),
            "summary": operation.get("summary"),
            "description": operation.get("description", ""),
            "security": operation.get("security", self.spec.get("security", [])),
            "parameters": parameters(self.spec, entry["item"], operation),
            "request_fields": fields(self.spec, request_schema(self.spec, operation), max_depth=4),
            "response_fields": fields(
                self.spec, response_schema(self.spec, operation), max_depth=4
            ),
            "externalDocs": operation.get("externalDocs", self.spec.get("externalDocs", {})),
            "projection_limits": "References expanded to depth 4; descriptions capped at 800 chars; maximum 180 response rows and 100 request rows. Truncation is explicit.",
        }
        for name, limit in (("response_fields", 180), ("request_fields", 100)):
            if len(result[name]) > limit:
                result[name] = result[name][:limit] + [
                    {"truncated": "row budget reached; request a schema reference"}
                ]
        return result
