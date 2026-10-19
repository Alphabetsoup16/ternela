# SPDX-License-Identifier: Apache-2.0
"""Run a generated plan against a JSON snapshot supplied by a read-only connector.

This local polling adapter is intentionally small. It does not execute the
proposed business action, refresh provider data, or provide durable scheduling.
"""

import argparse
import json
from pathlib import Path
import time

from .runtime import compile_plan, evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=Path(__file__).parent / "fixtures/public.json")
    parser.add_argument("--task", required=True)
    parser.add_argument("--snapshot", type=Path, required=True,
                        help="JSON object mapping tool IDs to data/observed_at/available envelopes")
    parser.add_argument("--phase", choices=("before", "after"), default="before")
    parser.add_argument("--accepted-at", type=float, help="Unix timestamp of action acceptance, for after-phase deadlines")
    parser.add_argument("--now", type=float, help="Fixed logical time for fixture replay; omitted uses wall clock")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    task = next(t for t in json.loads(args.tasks.read_text())["tasks"] if t["id"] == args.task)
    plan = next(p for p in json.loads(args.plans.read_text())["plans"] if p["task_id"] == args.task)
    compile_plan(plan, task)
    if args.phase == "after" and args.accepted_at is None:
        parser.error("--accepted-at is required after execution so receipt deadlines are meaningful")
    previous = None
    try:
        while True:
            now = args.now if args.now is not None else time.time()
            elapsed = max(0, now - args.accepted_at) if args.accepted_at is not None else 0
            adapter_error = None
            try:
                evidence = json.loads(args.snapshot.read_text())
                if not isinstance(evidence, dict):
                    raise ValueError("Snapshot must be an object")
            except (OSError, ValueError) as exc:
                evidence, adapter_error = {}, str(exc)
            result = evaluate(plan, task, evidence, args.phase, now, elapsed)
            state = (result["status"], tuple(result["reasons"]))
            if state != previous or args.once:
                print(json.dumps({"task_id": args.task, "phase": args.phase, "at": now,
                                  "adapter_error": adapter_error, **result}), flush=True)
            previous = state
            if args.once:
                break
            time.sleep(plan["poll_interval_seconds"])
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
