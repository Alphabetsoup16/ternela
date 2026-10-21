# SPDX-License-Identifier: Apache-2.0
"""Guard typed value preservation before a dependent observation executes."""

from ternela.evaluation import bind_observations as bind_observations
from ternela.evaluation import evaluate, execute
from ternela.guards import base_preservation_issues as preservation_issues
from ternela.guards import enforce_guard
from ternela.guards import quantity_slots as quantity_slots


def run_plan(plan, context, transport, phase="after"):
    observed = {}
    issues = preservation_issues(plan)
    if issues:
        return {
            "status": "unverifiable",
            "checks": [],
            "gaps": [{"limitation": s} for s in issues],
            "preservation_issues": issues,
        }, observed
    for request in plan["requests"]:
        try:
            for guard in plan.get("preservations", []):
                if guard["target_request_id"] == request["id"]:
                    enforce_guard(guard, request, context, observed)
            observed[request["id"]] = execute(request, context, transport, observed)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            observed[request["id"]] = {
                "http_status": 0,
                "body": None,
                "collection_complete": False,
                "traversal_reason": str(exc),
                "trace": [],
            }
    return evaluate(plan, observed, context, phase), observed
