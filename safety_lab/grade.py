# SPDX-License-Identifier: Apache-2.0
"""Replay the unchanged scenarios through the hardened runtime."""
import json
import sys
from pathlib import Path
from expansion_lab import grade as original
from expansion_lab.storage import Store, write_json, sha
from .runtime import run_plan, preservation_issues


def main():
    original.run_plan = run_plan
    original.preservation_issues = preservation_issues
    original.main()
    store = Store(); path = store.root / "runs" / sys.argv[1] / "manifest.json"; manifest = json.loads(path.read_text())
    audit = store.get(manifest["audit"])
    audit.update(post_fresh_regression=manifest.get("post_fresh_regression", False), hardening_runtime_sha256=sha((Path(__file__).parent / "runtime.py").read_bytes()))
    manifest["audit"] = store.put(audit); write_json(path, manifest)


if __name__ == "__main__":
    main()
