# SPDX-License-Identifier: Apache-2.0
"""Derive optional response shapes from official JSON examples, never from task text."""

import json
from copy import deepcopy
from html.parser import HTMLParser

from .pinned_sources import SourceIndex as PinnedIndex
from .specification import response_schema
from .storage import PROJECT, Store, sha, write_json

MANIFEST = PROJECT / "data/sources/supplements.json"


class Examples(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.buffer = []
        self.examples = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.depth += 1
            self.buffer = []

    def handle_data(self, value):
        if self.depth:
            self.buffer.append(value)

    def handle_endtag(self, tag):
        if tag == "pre" and self.depth:
            self.depth -= 1
            try:
                self.examples.append(json.loads("".join(self.buffer)))
            except ValueError:
                pass


def shape(example):
    """Presence in an example does not imply required, enum, or semantic guarantee."""
    if isinstance(example, dict):
        return {"type": "object", "properties": {k: shape(v) for k, v in example.items()}}
    if isinstance(example, list):
        return {"type": "array", "items": shape(example[0]) if example else {}}
    if example is None:
        return {}
    return {
        "type": (
            "boolean"
            if type(example) is bool
            else (
                "integer"
                if type(example) is int
                else "number"
                if type(example) is float
                else "string"
            )
        )
    }


def ingest(provider, operation, url, html, fetched_at):
    index = PinnedIndex(provider)
    entry = index.by_key[operation]
    external = entry["operation"].get("externalDocs", {}).get("url")
    if not external:
        raise ValueError("Operation does not link official documentation")
    # An explicit, reviewed official-domain redirect. No arbitrary example URL is trusted.
    from urllib.parse import urlsplit

    original, target = urlsplit(external), urlsplit(url)
    if not (
        provider == "slack"
        and original.hostname == "api.slack.com"
        and target.hostname == "docs.slack.dev"
        and target.path.strip("/") == "reference/methods/" + original.path.strip("/").split("/")[-1]
    ):
        raise ValueError("Supplement must be the operation's linked official documentation")
    parser = Examples()
    parser.feed(html)
    candidates = [
        v
        for v in parser.examples
        if isinstance(v, dict) and v.get("ok") is True and isinstance(v.get("user"), dict)
    ]
    if len(candidates) != 1:
        raise ValueError("Ambiguous or absent success example; manual source review needed")
    store = Store()
    document = store.put(html, "text/html")
    example = candidates[0]
    overlay = {
        "provider": provider,
        "operation": operation,
        "url": url,
        "linked_from": external,
        "fetched_at": fetched_at,
        "raw_document": document,
        "example": store.put(example),
        "schema": store.put(shape(example)),
        "policy": "Optional shape inferred from official success example; no required fields or semantic guarantees inferred",
        "original_response_sha256": sha(
            json.dumps(response_schema(index.spec, entry["operation"]), sort_keys=True).encode()
        ),
    }
    records = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else []
    records = [r for r in records if (r["provider"], r["operation"]) != (provider, operation)] + [
        overlay
    ]
    write_json(MANIFEST, records)
    return overlay


class SourceIndex(PinnedIndex):
    def __init__(self, provider):
        super().__init__(provider)
        self.supplements = []
        if not MANIFEST.exists():
            return
        store = Store()
        for record in json.loads(MANIFEST.read_text()):
            if record["provider"] != provider:
                continue
            entry = self.by_key[record["operation"]]
            original = response_schema(self.spec, entry["operation"])
            if (
                sha(json.dumps(original, sort_keys=True).encode())
                != record["original_response_sha256"]
            ):
                raise ValueError("Supplement was reviewed for a different response schema")
            # Verify raw provenance as well as the derived schema, every load.
            raw = store.get(record["raw_document"])
            parser = Examples()
            parser.feed(raw)
            example = store.get(record["example"])
            if example not in parser.examples or store.get(record["schema"]) != shape(example):
                raise ValueError("Derived shape does not match its source example")
            replacement = deepcopy(store.get(record["schema"]))
            replacement["description"] = record["policy"] + "; source: " + record["url"]
            # Replace the operation-local response projection, never the pinned file or global definition.
            original.clear()
            original.update(replacement)
            self.supplements.append(record)
