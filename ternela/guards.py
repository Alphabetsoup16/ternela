# SPDX-License-Identifier: Apache-2.0
"""Typed preservation and business-phase rules for admitted observations."""

import json

from .evaluation import bind_observations
from .expressions import bind, compare
from .specification import pointer


def quantity_slots(plan):
    slots = []
    for request in plan["requests"]:
        if request["operation"] != "POST /v1/invoices/create_preview":
            continue
        body = json.loads(request["bindings_json"]).get("body", {})
        for n, item in enumerate(body.get("subscription_details", {}).get("items", [])):
            if item.get("id") and item.get("price"):
                slots.append(
                    (request, body, item, f"/body/subscription_details/items/{n}/quantity")
                )
    return slots


def base_preservation_issues(plan):
    """Explicit provider rule: preserve observed quantity for existing-item price previews."""
    issues = []
    requests = {r["id"]: r for r in plan["requests"]}
    positions = {r["id"]: n for n, r in enumerate(plan["requests"])}
    if len(requests) != len(plan["requests"]):
        issues.append("Request IDs must be unique")
    for guard in plan.get("preservations", []):
        source, target = guard["source_request_id"], guard["target_request_id"]
        if (
            source not in requests
            or target not in requests
            or positions.get(source, 999) >= positions.get(target, -1)
        ):
            issues.append("Preservation source must be a declared earlier request: " + guard["id"])
        else:
            expected = "$response." + source + guard["source_pointer"]
            try:
                actual = pointer(
                    json.loads(requests[target]["bindings_json"]), guard["target_pointer"]
                )
            except (KeyError, IndexError, TypeError):
                actual = None
            if actual != expected:
                issues.append(
                    "Preserved target must use an exact typed response reference: " + guard["id"]
                )
            if not requests[source]["operation"].startswith("GET "):
                issues.append("Preservation source must be a read observation: " + guard["id"])
    for request, body, item, target in quantity_slots(plan):
        guards = [
            g
            for g in plan.get("preservations", [])
            if g["target_request_id"] == request["id"]
            and g["target_pointer"] == target
            and g["phase"] == "before"
        ]
        if not guards:
            issues.append(
                "Existing-item price preview lacks observed-quantity preservation at "
                + request["id"]
                + target
            )
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
            if guard["value_type"] != "integer" or not guard["source_pointer"].endswith(
                "/quantity"
            ):
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
    if (
        source.get("collection_complete") is not True
        or not 200 <= source.get("http_status", 0) < 300
    ):
        raise ValueError("Preservation dependency is incomplete")
    value = pointer(source["body"], guard["source_pointer"])
    types = {"integer": (int,), "number": (int, float), "string": (str,), "boolean": (bool,)}
    if type(value) not in types[guard["value_type"]]:
        raise ValueError("Preservation source has the wrong type")
    identities = bind(json.loads(guard["source_identity_json"]), context)
    if not identities:
        raise ValueError("Preservation identity conditions are missing")
    if not all(
        compare(pointer(source["body"], key), "eq", expected)
        for key, expected in identities.items()
    ):
        raise ValueError("Preservation source identity mismatch")
    raw = pointer(json.loads(request["bindings_json"]), guard["target_pointer"])
    expected_ref = "$response." + guard["source_request_id"] + guard["source_pointer"]
    if raw != expected_ref:
        raise ValueError("Preservation target is not a typed copy")
    actual = pointer(
        bind_observations(json.loads(request["bindings_json"]), context, observed),
        guard["target_pointer"],
    )
    if not compare(actual, "eq", value):
        raise ValueError("Preservation copy mismatch")


def preservation_issues(plan):
    issues = base_preservation_issues(plan)
    checks = {c["id"]: c for c in plan["checks"]}
    if len(checks) != len(plan["checks"]):
        issues.append("Check IDs must be unique")
    for requirement in plan["requirements"]:
        for identity in requirement["check_ids"]:
            if identity not in checks or checks[identity]["phase"] != requirement["phase"]:
                issues.append(
                    "Requirement check must exist in the same business phase: " + identity
                )
    for request, body, item, target in quantity_slots(plan):
        relevant = [c for c in plan["checks"] if c["request_id"] == request["id"]]
        if not relevant or any(c["phase"] != "before" for c in relevant):
            issues.append(
                "Existing-item price preview checks must gate BEFORE readiness, including budget and currency; no preview check may be deferred to AFTER"
            )
    return issues
