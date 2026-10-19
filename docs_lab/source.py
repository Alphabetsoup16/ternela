# SPDX-License-Identifier: Apache-2.0
"""Fetch complete provider specifications; index them without endpoint hand-selection."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).parent
SOURCES = {
    "stripe": ("stripe/openapi", "openapi/spec3.json"),
    "github": ("github/rest-api-description", "descriptions/api.github.com/api.github.com.json"),
    "slack": ("slackapi/slack-api-specs", "web-api/slack_web_openapi_v2_without_examples.json"),
}
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def get_json(url):
    # System curl uses the host's configured CA store; Python's local CA bundle
    # is unavailable here. TLS verification remains enabled.
    data = subprocess.check_output(["/usr/bin/curl", "--fail", "--silent", "--show-error",
                                    "--location", "--max-time", "60", "--max-filesize", "40000000",
                                    "--user-agent", "observation-docs-lab/1.0", url])
    return json.loads(data), data, {}


def fetch_one(provider):
    repo, path = SOURCES[provider]
    commits, _, _ = get_json(f"https://api.github.com/repos/{repo}/commits?path={path}&per_page=1")
    commit = commits[0]
    url = f"https://raw.githubusercontent.com/{repo}/{commit['sha']}/{path}"
    spec, raw, headers = get_json(url)
    destination = ROOT / "sources" / f"{provider}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return {"provider": provider, "repository": repo, "path": path, "url": url,
            "commit": commit["sha"], "source_commit_date": commit["commit"]["committer"]["date"],
            "downloaded_at": datetime.now(timezone.utc).isoformat(), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(), "info": spec.get("info", {}),
            "operations": sum(method in METHODS for item in spec["paths"].values() for method in item)}


def fetch():
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources = list(pool.map(fetch_one, SOURCES))
    (ROOT / "sources" / "manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    for source in sources:
        print(source["provider"], source["operations"], "operations; source committed", source["source_commit_date"], source["bytes"], "bytes")
    # Public read only: obtain a real run identity to test one generated GET plan later.
    run_list, raw, _ = get_json("https://api.github.com/repos/cli/cli/actions/runs?per_page=1")
    (ROOT / "sources" / "public-workflow-list.json").write_bytes(raw)
    if not run_list.get("workflow_runs"):
        raise ValueError("No public workflow run available")
    print("Public workflow run selected:", run_list["workflow_runs"][0]["id"])


def pointer(document, path):
    if path in ("", "#"):
        return document
    if path.startswith("#"):
        path = path[1:]
    if not path.startswith("/"):
        raise KeyError(path)
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        document = document[int(part)] if isinstance(document, list) else document[part]
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
    for key in ("type", "description", "enum", "format", "required", "nullable", "additionalProperties"):
        if key in node:
            val = node[key]
            entry[key] = val[:800] if key == "description" else val
    if ref:
        entry["$ref"] = ref
    rows = [entry]
    next_ancestors = ancestors + ((ref,) if ref else ())
    if depth == max_depth and any(k in node for k in ("properties", "items", "allOf", "oneOf", "anyOf")):
        entry["truncated"] = "depth budget; request schema reference for more"
        return rows
    for key in ("allOf", "oneOf", "anyOf"):
        for branch in node.get(key, []):
            rows.extend(fields(spec, branch, path, depth + 1, max_depth, next_ancestors))
    for name, child in node.get("properties", {}).items():
        rows.extend(fields(spec, child, path + "/" + name.replace("~", "~0").replace("/", "~1"), depth + 1, max_depth, next_ancestors))
    if "items" in node:
        items = node["items"]
        for item in items if isinstance(items, list) else [items]:
            rows.extend(fields(spec, item, path + "/*", depth + 1, max_depth, next_ancestors))
    return rows


def parameters(spec, path_item, operation):
    return [resolve(spec, p) for p in path_item.get("parameters", []) + operation.get("parameters", [])]


def request_schema(spec, operation):
    body = resolve(spec, operation.get("requestBody", {}))
    for content_type, entry in body.get("content", {}).items():
        if "json" in content_type or "form" in content_type:
            return entry.get("schema", {})
    return {}


def tokenize(text):
    # No provider/task synonyms or manually chosen endpoints.
    words = re.findall(r"[a-z][a-z0-9_]*", re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower())
    stop = {"the", "a", "an", "to", "of", "and", "in", "for", "is", "it", "that", "this", "be", "only", "with", "on", "or", "at", "as"}
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in stop]


class Index:
    def __init__(self, provider):
        self.provider = provider
        self.spec = json.loads((ROOT / "sources" / f"{provider}.json").read_text())
        self.entries = []
        for path, item in self.spec["paths"].items():
            for method, operation in item.items():
                if method not in METHODS:
                    continue
                # Automatic text extraction from original operations, without flattened response bodies.
                text = " ".join([method, path, operation.get("operationId", ""), operation.get("summary", ""),
                                 operation.get("description", ""), " ".join(operation.get("tags", [])),
                                 " ".join(p.get("name", "") + " " + p.get("description", "")
                                          for p in parameters(self.spec, item, operation))])
                tokens = Counter(tokenize(text))
                self.entries.append({"key": method.upper() + " " + path, "method": method,
                                     "path": path, "operation": operation, "item": item,
                                     "tokens": tokens, "length": sum(tokens.values())})
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
                    idf = math.log(1 + (len(self.entries) - self.df[word] + .5) / (self.df[word] + .5))
                    score += idf * count * 2.2 / (count + 1.2 * (.25 + .75 * entry["length"] / self.mean_length))
            results.append((score, entry["key"]))
        return [{"score": round(score, 5), "operation": key} for score, key in sorted(results, reverse=True)[:top_k]]

    def excerpt(self, key):
        entry = self.by_key[key]
        operation = entry["operation"]
        result = {"operation": key, "operationId": operation.get("operationId"),
                  "summary": operation.get("summary"), "description": operation.get("description", ""),
                  "security": operation.get("security", self.spec.get("security", [])),
                  "parameters": parameters(self.spec, entry["item"], operation),
                  "request_fields": fields(self.spec, request_schema(self.spec, operation), max_depth=4),
                  "response_fields": fields(self.spec, response_schema(self.spec, operation), max_depth=4),
                  "externalDocs": operation.get("externalDocs", self.spec.get("externalDocs", {})),
                  "projection_limits": "References expanded to depth 4; descriptions capped at 800 chars; maximum 180 response rows and 100 request rows. Truncation is explicit."}
        for name, limit in (("response_fields", 180), ("request_fields", 100)):
            if len(result[name]) > limit:
                result[name] = result[name][:limit] + [{"truncated": "row budget reached; request a schema reference"}]
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--restore", type=Path, help="Restore pinned sources from an earlier experiment.json")
    args = parser.parse_args()
    if args.fetch and args.restore:
        parser.error("Choose fresh acquisition or restoration")
    if args.fetch:
        fetch()
    if args.restore:
        sources = json.loads(args.restore.read_text())["sources"]
        for source in sources:
            repo, path = SOURCES[source["provider"]]
            commit = source["commit"]
            if not re.fullmatch(r"[a-f0-9]{40}", commit):
                raise ValueError("Invalid source commit")
            url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
            _, raw, _ = get_json(url)
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError("Pinned source hash mismatch")
            (ROOT / "sources" / f"{source['provider']}.json").write_bytes(raw)
        (ROOT / "sources/manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
        print("Restored pinned official source snapshots")


if __name__ == "__main__":
    main()
