# SPDX-License-Identifier: Apache-2.0
"""Generic interpreter. It never imports benchmark expectations or task policies."""

import math
import operator

from .schema import PLAN, validate_shape

OPS = {"eq": operator.eq, "ne": operator.ne, "lt": operator.lt,
       "lte": operator.le, "gt": operator.gt, "gte": operator.ge,
       "contains": lambda left, right: right in left}


def matches_type(value, kind):
    if kind == "number":
        return type(value) in (float, int) and math.isfinite(value)
    return type(value) is {"string": str, "boolean": bool, "array": list}[kind]


def compile_plan(plan, task):
    validate_shape(plan, PLAN)
    if plan["task_id"] != task["id"]:
        raise ValueError("Wrong task identity")
    catalog = {t["id"]: t for t in task["tools"]}
    ids = set()
    for check in plan["checks"]:
        if not check["id"] or check["id"] in ids:
            raise ValueError("Duplicate or empty check identity")
        ids.add(check["id"])
        tool = catalog.get(check["tool"])
        if tool is None or check["field"] not in tool["fields"]:
            raise ValueError("Unknown observation tool or field")
        kind = tool["fields"][check["field"]]["type"]
        if check["op"] in ("lt", "lte", "gt", "gte") and kind != "number":
            raise ValueError("Ordering requires a numeric field")
        if check["op"] == "contains":
            if kind not in ("string", "array") or type(check["value"]) is not str:
                raise ValueError("Contains requires a string value and string/array field")
        elif not matches_type(check["value"], kind):
            raise ValueError("Comparison value has the wrong type")
    for requirement in plan["requirements"]:
        if not set(requirement["check_ids"]) <= ids:
            raise ValueError("Requirement references a nonexistent check")
    if not plan["unresolved"] and not all(any(c["phase"] == phase for c in plan["checks"])
                                          for phase in ("before", "after")):
        raise ValueError("A supported plan must check both preconditions and completion")
    return plan


def evaluate(plan, task, evidence, phase="before", now=1000, elapsed_seconds=0):
    """Evidence envelopes are connector-owned: data, observed_at, available.

    Before: ready / blocked / unknown / unmonitorable.
    After: complete / pending / overdue / unmonitorable.
    A call evaluates a snapshot; the harness advances time and injects events.
    This is not an atomic execution API or a new durable scheduler.
    """
    compile_plan(plan, task)
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("Observation clock must be finite")
    if type(elapsed_seconds) not in (int, float) or not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("Elapsed time must be finite and nonnegative")
    if not isinstance(evidence, dict):
        evidence = {}
    if phase not in ("before", "after"):
        raise ValueError("Unknown phase")
    if plan["unresolved"]:
        return {"status": "unmonitorable", "reasons": plan["unresolved"],
                "tool_reads": 0, "cost_units": 0, "checks": []}
    tools = {t["id"]: t for t in task["tools"]}
    selected = [c for c in plan["checks"] if c["phase"] == phase]
    used_tools = set(c["tool"] for c in selected)
    results = []
    for check in selected:
        envelope = evidence.get(check["tool"])
        outcome, detail = "unknown", "source unavailable"
        if (isinstance(envelope, dict) and envelope.get("available", True) is True
                and isinstance(envelope.get("data"), dict)):
            observed = envelope.get("observed_at")
            value = envelope.get("data", {}).get(check["field"])
            kind = tools[check["tool"]]["fields"][check["field"]]["type"]
            if type(observed) not in (int, float) or not math.isfinite(observed):
                detail = "missing or invalid observation time"
            elif not 0 <= now - observed <= check["max_age_seconds"]:
                detail = "observation expired or is from the future"
            elif not matches_type(value, kind):
                detail = "missing or invalid field value"
            else:
                okay = OPS[check["op"]](value, check["value"])
                outcome = "true" if okay else "false"
                detail = f"{check['tool']}.{check['field']}: {value!r} {check['op']} {check['value']!r}"
        results.append({"id": check["id"], "outcome": outcome, "detail": detail,
                        "tool": check["tool"], "field": check["field"]})
    if phase == "before":
        status = ("blocked" if any(r["outcome"] == "false" for r in results) else
                  "unknown" if any(r["outcome"] == "unknown" for r in results) else "ready")
    else:
        status = ("complete" if all(r["outcome"] == "true" for r in results) else
                  "overdue" if elapsed_seconds >= plan["receipt_timeout_seconds"] else "pending")
    return {"status": status, "checks": results,
            "reasons": [r["id"] + ": " + r["detail"] for r in results if r["outcome"] != "true"],
            "tool_reads": len(used_tools),
            "cost_units": sum(tools[t]["cost_units"] for t in used_tools)}
