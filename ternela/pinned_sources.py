# SPDX-License-Identifier: Apache-2.0
"""Index every pinned operation directly from compressed official specifications."""

import gzip
import json

from .specification import Index, resolve, response_schema
from .storage import PROJECT, sha


class SourceIndex(Index):
    def __init__(self, provider):
        if provider not in {"stripe", "github", "slack"}:
            raise ValueError("Unsupported documentation provider")
        compressed = PROJECT / "data/sources" / (provider + ".json.gz")
        raw = gzip.decompress(compressed.read_bytes())
        manifest_path = PROJECT / "data/sources/manifest.json"
        pin = next(s for s in json.loads(manifest_path.read_text()) if s["provider"] == provider)
        if sha(raw) != pin["sha256"]:
            raise ValueError("Pinned source hash mismatch")
        super().__init__(provider, json.loads(raw))


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
        score = 2 * bool(
            refs & resource_refs(index.spec, response_schema(index.spec, entry["operation"]))
        )
        for path in paths:
            if (
                entry["path"].startswith(path + "/{")
                and entry["path"].count("/") == path.count("/") + 1
            ):
                score += 2
            if path.rsplit("/", 1)[0] == entry["path"]:
                score += 1
        if score:
            ranked.append((score, entry["key"]))
    return [
        {"operation": op, "graph_score": score}
        for score, op in sorted(ranked, key=lambda v: (-v[0], v[1]))[:limit]
    ]
