# SPDX-License-Identifier: Apache-2.0
"""Deterministic, verified gzip blobs with small portable run manifests."""
import gzip
import hashlib
import json
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


class Store:
    def __init__(self, root=None):
        self.root = Path(root or PROJECT / "data")

    def put(self, value, media_type="application/json"):
        data = canonical(value) if media_type == "application/json" else value.encode() if isinstance(value, str) else value
        digest = sha(data)
        path = self.root / "blobs" / digest[:2] / (digest + ".gz")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            packed = gzip.compress(data, compresslevel=9, mtime=0)
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            temporary.write_bytes(packed)
            temporary.replace(path)
        if sha(gzip.decompress(path.read_bytes())) != digest:
            raise ValueError("Corrupt content-addressed blob")
        return {"sha256": digest, "bytes": len(data), "gzip_bytes": path.stat().st_size, "media_type": media_type}

    def get(self, ref):
        digest = ref["sha256"]
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid blob reference")
        data = gzip.decompress((self.root / "blobs" / digest[:2] / (digest + ".gz")).read_bytes())
        if sha(data) != digest:
            raise ValueError("Corrupt content-addressed blob")
        return json.loads(data) if ref["media_type"] == "application/json" else data.decode()
