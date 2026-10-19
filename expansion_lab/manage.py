# SPDX-License-Identifier: Apache-2.0
"""Create the requested Repos project and retain a verified compressed history."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
from datetime import datetime, timezone

from .storage import write_json

ORIGIN = Path(__file__).resolve().parent.parent
MODULES = ["decision_lab", "observation_lab", "docs_lab", "qdrant_lab", "expansion_lab", "tests", "research"]


def ignore(directory, names):
    blocked = {"__pycache__", ".DS_Store"}
    if Path(directory) == ORIGIN / "docs_lab/sources":
        blocked.update(n for n in names if n.endswith(".json") and n != "manifest.json")
    return blocked


def sync(destination):
    for name in MODULES:
        shutil.copytree(ORIGIN / name, destination / name, dirs_exist_ok=True, ignore=ignore)
    for name in ["requirements.txt", ".gitignore"]:
        shutil.copy2(ORIGIN / name, destination / name)
    for provider in ["github", "stripe", "slack"]:
        raw = (ORIGIN / "docs_lab/sources" / (provider + ".json")).read_bytes()
        target = destination / "data/sources" / (provider + ".json.gz")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        if gzip.decompress(target.read_bytes()) != raw:
            raise ValueError("Source compression failed")
    shutil.copy2(ORIGIN / "docs_lab/sources/manifest.json", destination / "data/sources/manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    destination = args.destination.resolve()
    marker = destination / "project.json"
    if args.sync:
        if not marker.exists() or json.loads(marker.read_text())["origin"] != str(ORIGIN):
            raise ValueError("Refusing to update an unrelated folder")
        sync(destination)
        print("Synced code and pinned sources to", destination)
        return
    if destination.exists():
        raise ValueError("Choose a new project directory")
    destination.mkdir(parents=True)
    sync(destination)
    archive = destination / "data/history/legacy-experiments.tar.gz"
    archive.parent.mkdir(parents=True)
    files = []
    for name in MODULES[:-3] + ["tests", "research", "results"]:
        for p in (ORIGIN / name).rglob("*"):
            if not p.is_file() or p.is_symlink() or any(x in p.parts for x in ["__pycache__", "model-cache"]):
                continue
            if p.parent == ORIGIN / "docs_lab/sources" and p.name != "manifest.json":
                continue
            files.append(p)
    hashes = {str(p.relative_to(ORIGIN)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w|") as tar:
        for p in files:
            tar.add(p, arcname=str(p.relative_to(ORIGIN)), recursive=False)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if hashlib.sha256(tar.extractfile(member).read()).hexdigest() != hashes[member.name]:
                raise ValueError("Historical archive verification failed")
    write_json(archive.with_name("manifest.json"), {"files": hashes, "raw_bytes": sum(p.stat().st_size for p in files), "archive_bytes": archive.stat().st_size,
               "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "verified_all_members": True})
    generated = destination / "documents/generated/history"
    for p in (ORIGIN / "results").rglob("*.html"):
        target = generated / p.relative_to(ORIGIN / "results")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    write_json(marker, {"name": "Observation Lab", "origin": str(ORIGIN), "created_at": datetime.now(timezone.utc).isoformat(),
               "layout": {"code": "Python packages at project root", "generated_documents": "documents/generated", "compressed_sources": "data/sources", "compressed_run_blobs": "data/blobs", "run_manifests": "data/runs", "historical_archive": "data/history"},
               "history": "Original workspace left intact; historical report exports may refer to archived relative data. Restore archive for exact old layout. No secrets, dependency caches, or model weights copied."})
    print("Created", destination)
    print("History:", sum(p.stat().st_size for p in files), "bytes ->", archive.stat().st_size, "bytes; every archived member verified")


if __name__ == "__main__":
    main()
