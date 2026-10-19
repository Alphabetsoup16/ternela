# SPDX-License-Identifier: Apache-2.0
"""Index every pinned operation directly from compressed official specifications."""
from collections import Counter
import gzip
import json
from pathlib import Path

from docs_lab.source import Index, METHODS, parameters, tokenize, response_schema, resolve
from .storage import PROJECT, sha


class SourceIndex(Index):
    def __init__(self, provider):
        self.provider = provider
        compressed = PROJECT / "data/sources" / (provider + ".json.gz")
        raw = gzip.decompress(compressed.read_bytes()) if compressed.exists() else (PROJECT / "docs_lab/sources" / (provider + ".json")).read_bytes()
        manifest_path = PROJECT / "data/sources/manifest.json" if compressed.exists() else PROJECT / "docs_lab/sources/manifest.json"
        pin = next(s for s in json.loads(manifest_path.read_text()) if s["provider"] == provider)
        if sha(raw) != pin["sha256"]:
            raise ValueError("Pinned source hash mismatch")
        self.spec = json.loads(raw)
        self.entries = []
        for path, item in self.spec["paths"].items():
            for method, operation in item.items():
                if method not in METHODS:
                    continue
                text = " ".join([method, path, operation.get("operationId", ""), operation.get("summary", ""), operation.get("description", ""),
                                 " ".join(operation.get("tags", [])), " ".join(p.get("name", "") + " " + p.get("description", "") for p in parameters(self.spec, item, operation))])
                tokens = Counter(tokenize(text))
                self.entries.append({"key": method.upper() + " " + path, "method": method, "path": path, "operation": operation, "item": item, "tokens": tokens, "length": sum(tokens.values())})
        self.by_key = {e["key"]: e for e in self.entries}
        self.df = Counter(w for e in self.entries for w in e["tokens"])
        self.mean_length = sum(e["length"] for e in self.entries) / len(self.entries)


def resource_refs(spec, schema):
    """Root resource or immediate collection item refs, excluding incidental fields."""
    if "$ref" in schema:
        return {schema["$ref"]}
    node = resolve(spec, schema)
    found = set()
    if "items" in node and isinstance(node["items"], dict) and "$ref" in node["items"]:
        found.add(node["items"]["$ref"])
    for child in node.get("properties", {}).values():
        child = resolve(spec, child)
        if child.get("type") == "array" and "$ref" in child.get("items", {}):
            found.add(child["items"]["$ref"])
    return found


def neighbors(index, selected, limit=6):
    refs = set()
    paths = []
    for operation in selected:
        if operation in index.by_key:
            e = index.by_key[operation]
            refs.update(resource_refs(index.spec, response_schema(index.spec, e["operation"])))
            paths.append(e["path"])
    ranked = []
    for entry in index.entries:
        if entry["method"] != "get" or entry["key"] in selected:
            continue
        score = 2 * bool(refs & resource_refs(index.spec, response_schema(index.spec, entry["operation"])))
        for path in paths:
            if entry["path"].startswith(path + "/{") and entry["path"].count("/") == path.count("/") + 1:
                score += 2
            if path.rsplit("/", 1)[0] == entry["path"]:
                score += 1
        if score:
            ranked.append((score, entry["key"]))
    return [{"operation": op, "graph_score": score} for score, op in sorted(ranked, key=lambda v: (-v[0], v[1]))[:limit]]
