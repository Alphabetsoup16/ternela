# SPDX-License-Identifier: Apache-2.0
"""Guard typed value preservation before a dependent observation executes."""
import json

from docs_lab.source import pointer
from docs_lab.runtime import bind, compare
from docs_lab.v2.runtime import bind_observations, evaluate, execute


def quantity_slots(plan):
    slots = []
    for request in plan["requests"]:
        if request["operation"] != "POST /v1/invoices/create_preview":
            continue
        body = json.loads(request["bindings_json"]).get("body", {})
        for n, item in enumerate(body.get("subscription_details", {}).get("items", [])):
            if item.get("id") and item.get("price"):
                slots.append((request, body, item, f"/body/subscription_details/items/{n}/quantity"))
    return slots


def preservation_issues(plan):
    """Explicit provider rule: preserve observed quantity for existing-item price previews."""
    issues = []
    requests = {r["id"]: r for r in plan["requests"]}
    positions = {r["id"]: n for n, r in enumerate(plan["requests"])}
    if len(requests) != len(plan["requests"]):
        issues.append("Request IDs must be unique")
    for guard in plan.get("preservations", []):
        source, target = guard["source_request_id"], guard["target_request_id"]
        if source not in requests or target not in requests or positions.get(source, 999) >= positions.get(target, -1):
            issues.append("Preservation source must be a declared earlier request: " + guard["id"])
        else:
            expected = "$response." + source + guard["source_pointer"]
            try:
                actual = pointer(json.loads(requests[target]["bindings_json"]), guard["target_pointer"])
            except (KeyError, IndexError, TypeError):
                actual = None
            if actual != expected:
                issues.append("Preserved target must use an exact typed response reference: " + guard["id"])
            if not requests[source]["operation"].startswith("GET "):
                issues.append("Preservation source must be a read observation: " + guard["id"])
    for request, body, item, target in quantity_slots(plan):
        guards = [g for g in plan.get("preservations", []) if g["target_request_id"] == request["id"] and g["target_pointer"] == target and g["phase"] == "before"]
        if not guards:
            issues.append("Existing-item price preview lacks observed-quantity preservation at " + request["id"] + target)
            continue
        for guard in guards:
            try:
                identities = json.loads(guard["source_identity_json"])
                if not isinstance(identities, dict):
                    raise ValueError("Identity conditions must be an object")
            except (ValueError, TypeError):
                issues.append("Quantity preservation identity conditions must be a JSON object")
                continue
            parent = guard["source_pointer"].rsplit("/", 1)[0]
            if guard["value_type"] != "integer" or not guard["source_pointer"].endswith("/quantity"):
                issues.append("Quantity preservation requires an integer quantity source")
            if identities.get(parent + "/id") != item["id"]:
                issues.append("Quantity source must identify the exact subscription item")
            subscription_identity = identities.get(parent + "/subscription")
            if parent.startswith("/items/data/"):
                subscription_identity = identities.get("/id")
            if subscription_identity != body.get("subscription"):
                issues.append("Quantity source must identify the subscription")
    return issues


def enforce_guard(guard, request, context, observed):
    source = observed[guard["source_request_id"]]
    if source.get("collection_complete") is not True or not 200 <= source.get("http_status", 0) < 300:
        raise ValueError("Preservation dependency is incomplete")
    value = pointer(source["body"], guard["source_pointer"])
    types = {"integer": (int,), "number": (int, float), "string": (str,), "boolean": (bool,)}
    if type(value) not in types[guard["value_type"]]:
        raise ValueError("Preservation source has the wrong type")
    identities = bind(json.loads(guard["source_identity_json"]), context)
    if not identities:
        raise ValueError("Preservation identity conditions are missing")
    if not all(compare(pointer(source["body"], key), "eq", expected) for key, expected in identities.items()):
        raise ValueError("Preservation source identity mismatch")
    raw = pointer(json.loads(request["bindings_json"]), guard["target_pointer"])
    expected_ref = "$response." + guard["source_request_id"] + guard["source_pointer"]
    if raw != expected_ref:
        raise ValueError("Preservation target is not a typed copy")
    actual = pointer(bind_observations(json.loads(request["bindings_json"]), context, observed), guard["target_pointer"])
    if not compare(actual, "eq", value):
        raise ValueError("Preservation copy mismatch")


def run_plan(plan, context, transport, phase="after"):
    observed = {}
    issues = preservation_issues(plan)
    if issues:
        return {"status": "unverifiable", "checks": [], "gaps": [{"limitation": s} for s in issues], "preservation_issues": issues}, observed
    for request in plan["requests"]:
        try:
            for guard in plan.get("preservations", []):
                if guard["target_request_id"] == request["id"]:
                    enforce_guard(guard, request, context, observed)
            observed[request["id"]] = execute(request, context, transport, observed)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            observed[request["id"]] = {"http_status": 0, "body": None, "collection_complete": False, "traversal_reason": str(exc), "trace": []}
    return evaluate(plan, observed, context, phase), observed
