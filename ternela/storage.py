# SPDX-License-Identifier: Apache-2.0
"""Deterministic, verified gzip blobs with small portable run manifests."""

import gzip
import hashlib
import json
import math
import os
import uuid
from pathlib import Path

from .paths import DATA, PROJECT  # noqa: F401 — compatibility import


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def atomic_write(path, data):
    """Flush bytes before publishing their name, then flush directory metadata."""
    path = Path(path)
    # Persist newly created directory entries from the existing ancestor down.
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        sync_directory(directory.parent)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else DATA

    def put(self, value, media_type="application/json"):
        data = (
            canonical(value)
            if media_type == "application/json"
            else value.encode()
            if isinstance(value, str)
            else value
        )
        digest = sha(data)
        path = self.root / "blobs" / digest[:2] / (digest + ".gz")
        if not path.exists():
            packed = gzip.compress(data, compresslevel=9, mtime=0)
            atomic_write(path, packed)
        try:
            published = path.read_bytes()
        except FileNotFoundError:
            # Republish identical content once if a concurrent/mounted-filesystem
            # read loses visibility. Corrupt visible content must still fail.
            atomic_write(path, gzip.compress(data, compresslevel=9, mtime=0))
            published = path.read_bytes()
        if sha(gzip.decompress(published)) != digest:
            raise ValueError("Corrupt content-addressed blob")
        return {
            "sha256": digest,
            "bytes": len(data),
            "gzip_bytes": path.stat().st_size,
            "media_type": media_type,
        }

    def get(self, ref, *, max_bytes=None):
        digest = ref["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid blob reference")
        path = self.root / "blobs" / digest[:2] / (digest + ".gz")
        if max_bytes is None:
            data = gzip.decompress(path.read_bytes())
        else:
            if type(max_bytes) is not int or max_bytes < 0:
                raise ValueError("Blob read limit must be a nonnegative integer")
            with gzip.open(path, "rb") as stream:
                data = stream.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("Blob exceeds read limit")
        if sha(data) != digest:
            raise ValueError("Corrupt content-addressed blob")
        return json.loads(data) if ref["media_type"] == "application/json" else data.decode()


def strict_json(raw):
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON object key")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("JSON numbers must be finite")

    value = json.loads(raw, object_pairs_hook=object_pairs, parse_constant=invalid_constant)

    def inspect(item, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting exceeds 64 levels")
        if type(item) is float and not math.isfinite(item):
            raise ValueError("JSON numbers must be finite")
        children = (
            item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        )
        for child in children:
            inspect(child, depth + 1)

    inspect(value)
    return value
