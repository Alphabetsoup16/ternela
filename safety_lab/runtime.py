# SPDX-License-Identifier: Apache-2.0
"""Propagate guard failures to readiness and validate preview/check phases."""
import json

from expansion_lab.runtime import enforce_guard, quantity_slots, preservation_issues as original_issues
from docs_lab.v2.runtime import execute, evaluate


def preservation_issues(plan):
    issues = original_issues(plan)
    checks = {c["id"]: c for c in plan["checks"]}
    if len(checks) != len(plan["checks"]):
        issues.append("Check IDs must be unique")
    for requirement in plan["requirements"]:
        for identity in requirement["check_ids"]:
            if identity not in checks or checks[identity]["phase"] != requirement["phase"]:
                issues.append("Requirement check must exist in the same business phase: " + identity)
    for request, body, item, target in quantity_slots(plan):
        relevant = [c for c in plan["checks"] if c["request_id"] == request["id"]]
        if not relevant or any(c["phase"] != "before" for c in relevant):
            issues.append("Existing-item price preview checks must gate BEFORE readiness, including budget and currency; no preview check may be deferred to AFTER")
    return issues


def run_plan(plan, context, transport, phase="after"):
    issues = preservation_issues(plan)
    if issues:
        return {"status": "unverifiable", "checks": [], "gaps": [{"limitation": s} for s in issues], "preservation_issues": issues}, {}
    observed, failures = {}, []
    for request in plan["requests"]:
        guards = [g for g in plan.get("preservations", []) if g["target_request_id"] == request["id"]]
        try:
            for guard in guards:
                try:
                    enforce_guard(guard, request, context, observed)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    failures.append({"id": guard["id"], "phase": guard["phase"], "reason": str(exc)})
                    raise
            observed[request["id"]] = execute(request, context, transport, observed)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            observed[request["id"]] = {"http_status": 0, "body": None, "collection_complete": False, "traversal_reason": str(exc), "trace": []}
    result = evaluate(plan, observed, context, phase)
    failed_phase = [g for g in failures if g["phase"] == phase]
    if failed_phase:
        result.update(status="unknown", preservation_failures=failed_phase)
    return result, observed
