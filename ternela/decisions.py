# SPDX-License-Identifier: Apache-2.0
"""Deterministic, inspectable explanations; no model-generated evidence claims."""


def explain(watch):
    receipt = watch["receipt"]
    if receipt is None:
        return {
            "status": "unknown",
            "reason": "no_observation",
            "summary": "Waiting for the first observation.",
            "next_step": "Start a supported monitor or submit an observation.",
            "checks_passed": 0,
            "checks_total": len(watch["active_plan"]["checks"]),
            "blockers": [],
        }
    result = receipt["result"]
    checks = {check["id"]: check for check in watch["active_plan"]["checks"]}
    outcomes = result["checks"]
    passed = sum(item["outcome"] == "true" for item in outcomes)
    blockers = [
        {
            "id": item["id"],
            "outcome": item["outcome"],
            "description": checks[item["id"]]["reason"],
        }
        for item in outcomes
        if item["outcome"] != "true"
    ]
    status = result["status"]
    reason = "expired" if result.get("stale") else status
    messages = {
        "expired": ("Evidence expired.", "Obtain a fresh observation before proceeding."),
        "unknown": (
            "Evidence is missing or cannot establish the conditions.",
            "Inspect unavailable or incomplete observations and retry.",
        ),
        "unverifiable": (
            "The plan cannot verify every requirement.",
            "Inspect the coverage gaps; narrow the task or provide another evidence source.",
        ),
        "pending": (
            "Some observed completion conditions are not met.",
            "Continue observing the unmet conditions.",
        ),
        "blocked": (
            "Some observed preconditions are not met.",
            "Inspect the failed preconditions before taking the action.",
        ),
        "ready": (
            "Observed preconditions pass.",
            "Recheck freshness before taking the business action.",
        ),
        "complete": (
            "Observed completion conditions pass.",
            "Use the receipt and current resource identity when resuming the agent.",
        ),
    }
    summary, next_step = messages[reason]
    return {
        "status": status,
        "reason": reason,
        "summary": summary,
        "next_step": next_step,
        "checks_passed": passed,
        "checks_total": len(checks),
        "blockers": blockers,
    }
