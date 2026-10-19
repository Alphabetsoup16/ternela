# SPDX-License-Identifier: Apache-2.0
"""Replay the bundled known regression cases; never invoke a model."""
import json
from pathlib import Path
import tempfile

from expansion_lab.storage import PROJECT, Store
from expansion_lab import grade
from safety_lab.runtime import run_plan, preservation_issues
from source_lab.evidence import SourceIndex


def main():
    grade.run_plan=run_plan; grade.preservation_issues=preservation_issues
    catalog=json.loads((PROJECT/"examples/catalog.json").read_text())
    entries=[entry for entry in catalog if entry["task"]["id"].startswith("n")]
    tasks={entry["task"]["id"]:entry["task"] for entry in entries}
    plans={entry["task"]["id"]:entry["plan"] for entry in entries}
    private=json.loads((PROJECT/"expansion_lab/fresh/private.json").read_text())
    indexes={p:SourceIndex(p) for p in {task["provider"] for task in tasks.values()}}
    rows=grade.new_probes(tasks,plans,private,indexes)
    score=grade.score(rows)
    from docs_lab.audit import audit_check,audit_request
    issues=[]
    for identity,plan in plans.items():
        index=indexes[tasks[identity]["provider"]]; requests={r["id"]:r for r in plan["requests"]}
        issues+=preservation_issues(plan)
        for request in plan["requests"]:issues+=audit_request(index,request,tasks[identity],{})["issues"]
        for check in plan["checks"]:issues+=audit_check(index,check,requests.get(check["request_id"]))["issues"]
    print(json.dumps(dict(score,structural_issues=len(issues),scope="known regression; simulated provider responses"),indent=2))
    for row in rows:
        if not row["passed"]:print("FAIL",row["task_id"],row["case"],row["expected"],row["status"])
    if score["passed"]!=score["total"] or issues:raise SystemExit(1)


if __name__=="__main__": main()
