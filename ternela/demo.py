# SPDX-License-Identifier: Apache-2.0
"""A deterministic local demo: pending → complete → evidence expired."""

import tempfile
from pathlib import Path

from ternela.storage import Store
from ternela.watches import Watches
from workbench.demo import catalog, snapshots


def main():
    try:
        template = next(t for t in catalog() if t["task"]["id"] == "n02")
    except FileNotFoundError:
        raise SystemExit("Prepare public source evidence first: python3 -m ternela.setup_demo")
    with tempfile.TemporaryDirectory(prefix="ternela-demo-") as temporary:
        root = Path(temporary)
        now = [1000.0]
        engine = Watches(root / "watches.sqlite3", Store(root / "data"), clock=lambda: now[0])
        try:
            watch = engine.create(template["task"], template["plan"], "after", ttl=30)
        except FileNotFoundError:
            raise SystemExit("Run python3 -m ternela.setup_demo first")
        for revision, case in [(1, "processing"), (2, "success")]:
            body = snapshots(template["task"], template["plan"], case, "after")
            result = engine.submit(watch["id"], f"event-{revision}", revision, body)
            print(f"{case:18} -> {result['receipt']['result']['status']}")
        now[0] += 31
        result = engine.get(watch["id"])
        print(f"{'evidence expired':18} -> {result['receipt']['result']['status']}")
        messages = engine.outbox(watch["id"])
        print(
            f"{len(messages)} durable decision changes; simulated provider responses; no model call."
        )


if __name__ == "__main__":
    main()
