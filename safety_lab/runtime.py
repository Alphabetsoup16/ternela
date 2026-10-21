# SPDX-License-Identifier: Apache-2.0
"""Propagate guard failures to readiness and validate preview/check phases."""

from ternela.evaluation import evaluate, execute
from ternela.guards import base_preservation_issues as original_issues  # noqa: F401 — historical import
from ternela.guards import enforce_guard, preservation_issues
from ternela.guards import quantity_slots as quantity_slots


def run_plan(plan, context, transport, phase="after"):
    issues = preservation_issues(plan)
    if issues:
        return {
            "status": "unverifiable",
            "checks": [],
            "gaps": [{"limitation": s} for s in issues],
            "preservation_issues": issues,
        }, {}
    observed, failures = {}, []
    for request in plan["requests"]:
        guards = [
            g for g in plan.get("preservations", []) if g["target_request_id"] == request["id"]
        ]
        try:
            for guard in guards:
                try:
                    enforce_guard(guard, request, context, observed)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    failures.append(
                        {"id": guard["id"], "phase": guard["phase"], "reason": str(exc)}
                    )
                    raise
            observed[request["id"]] = execute(request, context, transport, observed)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            observed[request["id"]] = {
                "http_status": 0,
                "body": None,
                "collection_complete": False,
                "traversal_reason": str(exc),
                "trace": [],
            }
    result = evaluate(plan, observed, context, phase)
    failed_phase = [g for g in failures if g["phase"] == phase]
    if failed_phase:
        result.update(status="unknown", preservation_failures=failed_phase)
    return result, observed
