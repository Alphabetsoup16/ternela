# SPDX-License-Identifier: Apache-2.0
"""Revalidate saved plans; recompile only on public contract/schema feedback."""
import argparse
import json
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timezone

from expansion_lab.inference import infer
from expansion_lab.schema import BATCH
from expansion_lab.source import SourceIndex
from expansion_lab.storage import PROJECT, Store, sha, write_json
from docs_lab.audit import audit_check
from .runtime import preservation_issues


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("source_run"); parser.add_argument("--run-id", required=True); args = parser.parse_args()
    store = Store(); origin = json.loads((store.root / "runs" / args.source_run / "manifest.json").read_text())
    if origin["status"] != "completed": raise ValueError("Original run must be completed")
    path = store.root / "runs" / args.run_id / "manifest.json"
    if path.exists(): raise ValueError("Preserve existing runs")
    tasks = json.loads((PROJECT / origin["fixtures"] / "public.json").read_text())["tasks"]
    by_id = {t["id"]: t for t in tasks}; indexes = {p: SourceIndex(p) for p in {t["provider"] for t in tasks}}
    batch = store.get(origin["plans"]); feedback = {}
    for plan in batch["plans"]:
        requests = {r["id"]: r for r in plan["requests"]}
        issues = preservation_issues(plan)
        for check in plan["checks"]:
            audit = audit_check(indexes[by_id[plan["task_id"]]["provider"]], check, requests.get(check["request_id"]))
            issues.extend(audit["issues"])
        feedback[plan["task_id"]] = issues
    manifest = {k: v for k, v in origin.items() if k not in ["id", "stages", "audit", "plans", "started_at", "finished_at", "expansions", "reuse_from"]}
    manifest.update(id=args.run_id, arm="adaptive-hardened", origin_run=args.source_run, post_fresh_regression=True,
                    stages={}, expansions=[], started_at=datetime.now(timezone.utc).isoformat(), status="running", public_validation_feedback=store.put(feedback),
                    hardening_code={str(p.relative_to(PROJECT)): {"sha256": sha(p.read_bytes()), "snapshot": store.put(p.read_text(), "text/plain")} for p in Path(__file__).parent.glob("*.py")})
    write_json(path, manifest)
    try:
        if any(feedback.values()):
            stages = [s for name, s in origin["stages"].items() if name.startswith("compile-") and s["status"] == "completed"]
            payload = store.get(stages[-1]["input"])
            payload.update(previous_plan=batch, public_validation_feedback=feedback)
            prompt = (PROJECT / "docs_lab/v2/plan.txt").read_text() + (PROJECT / "expansion_lab/plan-extra.txt").read_text() + "\n\nPOST-FRESH CONTRACT REPAIR:\nThese are regression tasks. Use only public contract/schema validation feedback; no scenario results are supplied. All checks of an existing-item price-only invoice preview MUST be BEFORE, including currency and budget. The business action is the subscription change; observing the non-persisting preview precedes that action. Requirements referencing those checks must use the same BEFORE phase. Final actual-invoice checks remain AFTER. A failed preservation guard makes its business phase unknown regardless of other checks. Do not emit schema-unsupported field checks; an ambiguous vendor schema is an evidence gap and must remain explicit partial/unobservable coverage, rather than guessing paths from memory.\n"
            schema = deepcopy(BATCH); schema["properties"]["plans"].update(minItems=len(tasks), maxItems=len(tasks))
            batch = infer(payload, "contract-repair", schema, prompt, path, manifest, store)
        else:
            manifest["reused_final_plan"] = "No public contract/schema violation; original plan bytes retained"
        manifest["plans"] = store.put(batch); manifest["status"] = "completed"
    except Exception as exc:
        manifest.update(status="failed", error=str(exc)); raise
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat(); write_json(path, manifest)
    print("Saved post-fresh regression", args.run_id)


if __name__ == "__main__":
    main()
