# SPDX-License-Identifier: Apache-2.0
"""Reproduce bounded anonymous connector reads and local payload measurements.

No publication or artifact download. Saves full receipts in ignored verified gzip
blobs and a small report under data/audits/public-connectors/.
"""

import gzip
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from expansion_lab.storage import PROJECT, Store, canonical, write_json
from ternela.connectors import release_plan
from workbench.engine import Watches
from workbench.live import collect

CASES = (
    ("pypi", "httpx", "0.28.1", "complete"),
    ("pypi", "packaging", "25.0", "complete"),
    ("npm", "lodash", "4.17.21", "complete"),
    ("npm", "@types/node", "22.10.0", "complete"),
    ("pypi", "httpx", "9999.0.0-ternela-probe", "unknown"),
    ("npm", "lodash", "9999.0.0-ternela-probe", "unknown"),
)


def main():
    output = PROJECT / "data/audits/public-connectors"
    store = Store(output)
    report = {"python": platform.python_version(), "observed_at": time.time(), "cases": []}
    with tempfile.TemporaryDirectory(prefix="registry-probe-") as temporary:
        engine = Watches(Path(temporary) / "watches.sqlite3", store)
        for provider, package, version, expected in CASES:
            task, plan = release_plan(provider, package, version)
            watch = engine.create(task, plan, ttl=900)
            started = time.perf_counter()
            captured, at = collect(plan, task["context"], "after", provider)
            result = engine.submit(
                watch["id"], "probe", 1, captured, at, f"live public {provider} GET"
            )
            receipt = result["receipt"]
            report["cases"].append(
                dict(
                    provider=provider,
                    package=package,
                    version=version,
                    expected=expected,
                    actual=receipt["result"]["status"],
                    http_status=[
                        page["http_status"] for pages in captured.values() for page in pages
                    ],
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
                    wire_bytes=sum(
                        page.get("wire_bytes", 0) for pages in captured.values() for page in pages
                    ),
                    receipt=store.put(receipt),
                )
            )
            if package == "httpx" and expected == "complete":
                # Same watch and transition count for every representation comparison.
                full = engine.get(watch["id"])
                full["receipt_text"] = json.dumps(full["receipt"], indent=2)
                old = (
                    json.dumps(full).encode()
                    + json.dumps(engine.outbox(watch["id"], recent=True)).encode()
                )
                compact = canonical(engine.status(watch["id"]))
                compact_outbox = canonical(engine.outbox(watch["id"], recent=True, compact=True))
                report["payload"] = dict(
                    full_with_receipt_text_and_outbox_bytes=len(old),
                    compact_status_and_outbox_bytes=len(compact) + len(compact_outbox),
                    compact_gzip_level3_bytes=len(gzip.compress(compact, compresslevel=3, mtime=0))
                    + len(gzip.compress(compact_outbox, compresslevel=3, mtime=0)),
                )
                raw = canonical(receipt)
                report["receipt_compression"] = {"raw_bytes": len(raw), "levels": {}}
                for level in (3, 6, 9):
                    times = []
                    for _ in range(30):
                        before = time.perf_counter()
                        packed = gzip.compress(raw, compresslevel=level, mtime=0)
                        times.append((time.perf_counter() - before) * 1000)
                    report["receipt_compression"]["levels"][str(level)] = dict(
                        bytes=len(packed), median_ms=round(statistics.median(times), 3)
                    )
    report["passed"] = sum(case["actual"] == case["expected"] for case in report["cases"])
    report["scope"] = (
        "Six live metadata reads, including a scoped npm package and two missing versions; no provider mutations, artifact verification, model inference, or load test. Compression uses one same-state PyPI watch."
    )
    write_json(output / "report.json", report)
    print(json.dumps(report, indent=2))
    if report["passed"] != len(CASES):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
