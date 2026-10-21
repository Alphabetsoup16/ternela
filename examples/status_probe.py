# SPDX-License-Identifier: Apache-2.0
"""Reproduce the frozen cases and three anonymous live status-summary reads."""

import gzip
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import PROJECT, Store, canonical, sha, write_json
from ternela.connectors import CONNECTORS
from workbench.engine import Watches, observe
from workbench.live import collect


def main():
    output = PROJECT / "data/audits/github-status"
    connector = CONNECTORS.get("github_status")
    manifest = json.loads((PROJECT / "evals/github_status_v1.manifest.json").read_text())
    packed = (PROJECT / "evals/github_status_v1.json.gz").read_bytes()
    raw = gzip.decompress(packed)
    if sha(raw) != manifest["raw_sha256"] or sha(packed) != manifest["gzip_sha256"]:
        raise ValueError("Frozen status corpus hash mismatch")
    cases = json.loads(raw)["cases"]
    report = {
        "scope": "Frozen implementation-authored cases plus live vendor-metadata smoke reads; not independent annotation, live outage induction or a health guarantee.",
        "corpus_sha256": manifest["raw_sha256"],
        "cases": [],
        "live": [],
    }
    hashes = set()
    for case in cases:
        task, plan = connector.recipe(**case["context"])
        hashes.add(sha(canonical(plan)))
        with patch("ternela.connectors.read_json", return_value=deepcopy(case["envelope"])):
            captured, _ = collect(plan, task["context"], "before", "github_status")
        result, _ = observe(plan, task["context"], captured, "before")
        report["cases"].append(
            {"id": case["id"], "expected": case["expected"], "actual": result["status"]}
        )
    report["passed"] = sum(case["actual"] == case["expected"] for case in report["cases"])
    report["distinct_case_plans"] = len(hashes)
    store = Store(output)
    with tempfile.TemporaryDirectory(prefix="status-probe-") as directory:
        engine = Watches(Path(directory) / "state.sqlite3", store)
        for component_id in ("br0l2tvcx85d", "brv1bkgrwx7q", "missing00001"):
            task, plan = connector.recipe(component_id)
            watch = engine.create(task, plan, phase="before", ttl=900)
            snapshots, at = collect(plan, task["context"], "before", "github_status")
            receipt = engine.submit(
                watch["id"], "live-smoke", 1, snapshots, at, "live public github_status GET"
            )["receipt"]
            report["live"].append(
                {
                    "component_id": component_id,
                    "decision": receipt["result"]["status"],
                    "http_status": [
                        page["http_status"] for pages in snapshots.values() for page in pages
                    ],
                    "receipt": store.put(receipt),
                }
            )
    write_json(output / "probe.json", report)
    print(json.dumps(report, indent=2))
    if report["passed"] != len(cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
