# SPDX-License-Identifier: Apache-2.0
"""Measure local watch operations with synthetic evidence and temporary storage."""

import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path

from ternela.storage import Store
from ternela.watches import Watches
from workbench.demo import catalog, snapshots


def measure(iterations=50):
    template = next(item for item in catalog() if item["task"]["id"] == "n02")
    with tempfile.TemporaryDirectory(prefix="evidence-benchmark-") as temporary:
        root = Path(temporary)
        engine = Watches(root / "watches.sqlite3", Store(root / "data"))
        timings = []
        for _ in range(iterations + 1):
            started = time.perf_counter()
            watch = engine.create(template["task"], template["plan"], ttl=86400)
            timings.append((time.perf_counter() - started) * 1000)
        operations = {
            "cold_create_ms": round(timings[0], 3),
            "warm_create_median_ms": round(statistics.median(timings[1:]), 3),
        }
        for name in ("submit", "get", "outbox"):
            timings = []
            for revision in range(1, iterations + 1):
                started = time.perf_counter()
                if name == "submit":
                    evidence = snapshots(
                        template["task"],
                        template["plan"],
                        "success" if revision % 2 else "processing",
                        "after",
                    )
                    engine.submit(watch["id"], str(revision), revision, evidence)
                elif name == "get":
                    engine.get(watch["id"])
                else:
                    engine.outbox(watch["id"], after=max(0, iterations - 10))
                timings.append((time.perf_counter() - started) * 1000)
            operations[name + "_median_ms"] = round(statistics.median(timings), 3)
    return {
        "iterations": iterations,
        "python": platform.python_version(),
        "platform": platform.system(),
        "scope": "one local process; synthetic payment evidence; temporary SQLite and gzip storage; no network or model",
        **operations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=50)
    iterations = parser.parse_args().iterations
    if not 1 <= iterations <= 10000:
        parser.error("Iterations must be 1–10000")
    print(json.dumps(measure(iterations), indent=2))


if __name__ == "__main__":
    main()
