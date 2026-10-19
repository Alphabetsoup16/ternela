# SPDX-License-Identifier: Apache-2.0
"""Grade already-generated plans. This module is never sent to the planner."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .runtime import compile_plan, evaluate

FIXTURES = Path(__file__).parent / "fixtures"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixtures():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    for name, expected in manifest.items():
        if sha(FIXTURES / name) != expected:
            raise ValueError(f"Frozen fixture hash mismatch: {name}")
    public = json.loads((FIXTURES / "public.json").read_text())
    private = json.loads((FIXTURES / "private.json").read_text())
    return public, private, manifest


def materialize(suite, case):
    evidence = copy.deepcopy(suite["base"])
    for tool, patch in case["patches"].items():
        evidence[tool]["data"].update(patch)
    for tool in case["unavailable"]:
        evidence[tool]["available"] = False
    for tool in case["stale"]:
        evidence[tool]["observed_at"] = 880
    for tool, field in case["missing"]:
        evidence[tool]["data"].pop(field)
    return evidence


def summarize(rows):
    achievable = [r for r in rows if r["group"] != "unobservable_truth"]
    bad_clearances = [r for r in rows if r["phase"] == "before" and r["expected"] in ("blocked", "unknown", "unmonitorable")]
    false_completion = [r for r in rows if r["phase"] == "after" and r["expected"] != "complete"]
    safe = [r for r in rows if r["expected"] == "ready"]
    before = [r for r in rows if r["phase"] == "before" and r["expected"] != "unmonitorable"]
    return {
        "passed": sum(r["passed"] for r in rows), "total": len(rows),
        "observable_passed": sum(r["passed"] for r in achievable), "observable_total": len(achievable),
        "unsafe_clearances": sum(r["actual"] == "ready" for r in bad_clearances),
        "unsafe_clearance_opportunities": len(bad_clearances),
        "false_completions": sum(r["actual"] == "complete" for r in false_completion),
        "false_completion_opportunities": len(false_completion),
        "unnecessary_interruptions": sum(r["actual"] != "ready" for r in safe),
        "feasible_scenarios": len(safe),
        "invalid_plan_scenarios": sum(r["actual"] == "invalid_plan" for r in rows),
        "mean_before_tool_reads": round(sum(r["tool_reads"] for r in before) / len(before), 3) if before else None,
        "mean_before_cost_units": round(sum(r["cost_units"] for r in before) / len(before), 3) if before else None,
        "groups": {group: {"passed": sum(r["passed"] for r in group_rows), "total": len(group_rows)}
                   for group in sorted({r["group"] for r in rows})
                   for group_rows in [[r for r in rows if r["group"] == group]]},
    }


def grade(directory, public, private, manifest):
    metadata = json.loads((directory / "metadata.json").read_text())
    if metadata["status"] != "completed":
        raise ValueError(f"Inference did not complete: {directory}")
    if metadata["public_input_sha256"] != manifest["public.json"]:
        raise ValueError("Planner saw different public task inputs")
    if metadata["plans_sha256"] != sha(directory / "plans.json"):
        raise ValueError("Generated plans were modified after inference")
    plans = {p["task_id"]: p for p in json.loads((directory / "plans.json").read_text())["plans"]}
    tasks = {t["id"]: t for t in public["tasks"]}
    rows, task_results = [], []
    for suite in private["suites"]:
        task, plan = tasks[suite["task_id"]], plans[suite["task_id"]]
        validation_error = None
        try:
            compile_plan(plan, task)
        except ValueError as exc:
            validation_error = str(exc)
        for case in suite["cases"]:
            if validation_error:
                result = {"status": "invalid_plan", "reasons": [validation_error], "tool_reads": 0,
                          "cost_units": 0, "checks": []}
            else:
                result = evaluate(plan, task, materialize(suite, case), phase=case["phase"],
                                  now=case["now"], elapsed_seconds=case["elapsed_seconds"])
            rows.append({"task_id": task["id"], "case": case["id"], "phase": case["phase"],
                         "group": case["group"], "expected": case["expected"], "actual": result["status"],
                         "passed": result["status"] == case["expected"], "note": case["note"],
                         "tool_reads": result["tool_reads"], "cost_units": result["cost_units"],
                         "checks": result["checks"], "reasons": result["reasons"]})
        task_rows = [r for r in rows if r["task_id"] == task["id"]]
        task_results.append({"task_id": task["id"], "summary": summarize(task_rows),
                             "check_count": len(plan["checks"]), "catalog_tools": len(task["tools"]),
                             "before_tools": sorted({c["tool"] for c in plan["checks"] if c["phase"] == "before"}),
                             "after_tools": sorted({c["tool"] for c in plan["checks"] if c["phase"] == "after"}),
                             "unresolved": plan["unresolved"], "validation_error": validation_error})
    return {"name": directory.name, "directory": str(directory), "metadata": metadata,
            "summary": summarize(rows), "tasks": task_results, "runs": rows, "plans": plans}


def timeline(method, public, private):
    """Replay a billing lifecycle through the generated monitor, no replanning."""
    task = next(t for t in public["tasks"] if t["id"] == "t01")
    suite = next(s for s in private["suites"] if s["task_id"] == "t01")
    plan = method["plans"]["t01"]
    output, previous = [], None
    for identity in ("base", "affordable_increase", "one_cent_over", "preview_offline", "base", "accepted_not_paid", "final_confirmed"):
        case = next(c for c in suite["cases"] if c["id"] == identity)
        try:
            result = evaluate(plan, task, materialize(suite, case), case["phase"], case["now"], case["elapsed_seconds"])
        except ValueError as exc:
            result = {"status": "invalid_plan", "reasons": [str(exc)]}
        state = (case["phase"], result["status"])
        output.append({"event": identity, "phase": case["phase"], "status": result["status"],
                       "state_transition": previous is not None and state != previous,
                       "reasons": result["reasons"]})
        previous = state
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results/planning/latest.json"))
    args = parser.parse_args()
    public, private, manifest = load_fixtures()
    methods = [grade(directory, public, private, manifest) for directory in args.directories]
    review = json.loads((FIXTURES / "adjudication.json").read_text())
    disputed = set(review["review_required_task_ids"])
    for method in methods:
        method["uncontested_summary"] = summarize([r for r in method["runs"] if r["task_id"] not in disputed])
    source_hashes = {str(p): sha(p) for p in sorted(Path(__file__).parent.rglob("*.py"))}
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "fixture_manifest": manifest,
              "source_hashes": source_hashes, "tasks": public["tasks"], "methods": methods,
              "post_score_review": review,
              "post_score_review_sha256": sha(FIXTURES / "adjudication.json"),
              "scope": {
                  "real_model_inference": True, "real_provider_connections": False,
                  "independent_test_author": False, "model_has_private_cases": False,
                  "single_generation_per_strategy": True, "model_repair_after_grading": False,
                  "runtime": "Generic Python interpreter; synthetic observations and logical time, no Temporal or FreshCtx integration",
                  "cost": "Catalog cost units are synthetic. Token usage and generation wall time are actual; no dollar or production latency claim.",
                  "truth_limit": "t02/lying_preview is indistinguishable from t02/base using the available observations.",
                  "limits": "One model backend and one batch per strategy; prompts share the same authored suite; not independent held-out market or model validation. Purchase-task expectations are disputed; see post_score_review.",
              },
              "timeline": timeline(methods[-1], public, private)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    demo_dir = args.output.parent / "demo"
    demo_dir.mkdir(exist_ok=True)
    billing = next(s for s in private["suites"] if s["task_id"] == "t01")
    for name, case_id in (("ready", "base"), ("blocked", "one_cent_over"),
                          ("pending", "accepted_not_paid"), ("complete", "final_confirmed")):
        case = next(c for c in billing["cases"] if c["id"] == case_id)
        (demo_dir / f"billing-{name}.json").write_text(json.dumps(materialize(billing, case), indent=2) + "\n")
    for method in methods:
        print(method["name"], json.dumps(method["summary"]))
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
