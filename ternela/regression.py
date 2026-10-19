# SPDX-License-Identifier: Apache-2.0
"""Replay the bundled known regression cases; never invoke a model."""

import json

from expansion_lab import grade
from expansion_lab.storage import PROJECT
from source_lab.evidence import SourceIndex

from .validation import admit


def main():
    catalog = json.loads((PROJECT / "examples/catalog.json").read_text())
    entries = [entry for entry in catalog if entry["task"]["id"].startswith("n")]
    tasks = {entry["task"]["id"]: entry["task"] for entry in entries}
    plans = {entry["task"]["id"]: entry["plan"] for entry in entries}
    private = json.loads((PROJECT / "expansion_lab/fresh/private.json").read_text())
    indexes = {p: SourceIndex(p) for p in {task["provider"] for task in tasks.values()}}
    rows = grade.new_probes(tasks, plans, private, indexes)
    score = grade.score(rows)
    issues = []
    for identity, plan in plans.items():
        try:
            admit(plan, tasks[identity], indexes)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            issues.append(str(exc))
    print(
        json.dumps(
            dict(
                score,
                structural_issues=len(issues),
                scope="known regression; simulated provider responses",
            ),
            indent=2,
        )
    )
    for row in rows:
        if not row["passed"]:
            print("FAIL", row["task_id"], row["case"], row["expected"], row["status"])
    if score["passed"] != score["total"] or issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
