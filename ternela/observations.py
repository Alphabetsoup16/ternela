# SPDX-License-Identifier: Apache-2.0
"""Pure phase selection, observation execution, evaluation and receipt projections."""

import hashlib
import json
from copy import deepcopy

from .evaluation import evaluate, execute
from .guards import enforce_guard
from .validation import expression_values


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def phase_plan(plan, phase):
    """Observe only the requested phase and its transitive data dependencies."""
    result = deepcopy(plan)
    result["checks"] = [c for c in plan["checks"] if c["phase"] == phase]
    result["requirements"] = [r for r in plan["requirements"] if r["phase"] == phase]
    needed = {c["request_id"] for c in result["checks"]}
    by_id = {r["id"]: r for r in plan["requests"]}
    for _ in range(len(by_id) + 1):
        old = set(needed)
        for guard in plan["preservations"]:
            if guard["target_request_id"] in needed:
                needed.add(guard["source_request_id"])
        for identity in list(needed):
            if identity not in by_id:
                raise ValueError("Missing request dependency")
            bindings = json.loads(by_id[identity]["bindings_json"])
            needed.update(
                value[len("$response.") :].partition("/")[0]
                for value in expression_values(bindings)
                if value.startswith("$response.")
            )
        if needed == old:
            break
    result["requests"] = [r for r in plan["requests"] if r["id"] in needed]
    result["preservations"] = [g for g in plan["preservations"] if g["target_request_id"] in needed]
    return result


def observe(plan, context, snapshots, phase):
    def factory(request):
        pages = snapshots.get(request["id"], [])
        cursor = iter([pages] if isinstance(pages, dict) else pages)

        def transport(call):
            return deepcopy(next(cursor, {"http_status": 0, "body": None}))

        return transport

    return run_observations(plan, context, phase, factory)


def run_observations(plan, context, phase, transport_factory):
    """Apply the same dependency and preservation rules to acquisition and replay."""
    selected = phase_plan(plan, phase)
    observed, failures = {}, []
    for request in selected["requests"]:
        try:
            for guard in selected["preservations"]:
                if guard["target_request_id"] == request["id"]:
                    try:
                        enforce_guard(guard, request, context, observed)
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        failures.append({"id": guard["id"], "reason": str(exc)})
                        raise
            observed[request["id"]] = execute(
                request, context, transport_factory(request), observed
            )
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            observed[request["id"]] = {
                "http_status": 0,
                "body": None,
                "collection_complete": False,
                "traversal_reason": str(exc),
                "trace": [],
            }
    result = evaluate(selected, observed, context, phase)
    if failures:
        result.update(status="unknown", preservation_failures=failures)
    return result, observed


def receipt_summary(receipt):
    if receipt is None:
        return None
    result = {
        key: receipt[key]
        for key in (
            "event_id",
            "revision",
            "observed_at",
            "evaluated_at",
            "expires_at",
            "phase",
            "result",
            "plan_sha256",
            "mode",
        )
    }
    result["read_attempts"] = sum(
        len(o.get("trace", [])) for o in receipt.get("observations", {}).values()
    )
    return result


def expired_receipt(receipt, now):
    result = deepcopy(receipt)
    result["evaluated_at"] = now
    result["result"] = {
        "status": "unknown",
        "checks": [],
        "gaps": [{"limitation": "Evidence expired; obtain a new observation"}],
        "stale": True,
    }
    return result
