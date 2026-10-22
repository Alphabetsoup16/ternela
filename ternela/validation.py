# SPDX-License-Identifier: Apache-2.0
"""Shared admission rules for model output and persistent watches."""

import json

from ternela.guards import preservation_issues
from ternela.plan_audit import audit_check, audit_pagination, audit_request
from ternela.plan_schema import PLAN
from ternela.schema import validate_shape
from ternela.storage import PROJECT, canonical, sha, strict_json

from .connectors import SOURCE_FILES, source_index
from .errors import AdmissionError
from .paths import source as source_path


def admit(plan, task, indexes=None):
    validate_shape(plan, PLAN)
    if plan["task_id"] != task["id"]:
        raise ValueError("Plan/task identity mismatch")
    index = (indexes or {}).get(task["provider"]) or source_index(task["provider"])
    requests = {r["id"]: r for r in plan["requests"]}
    for request in plan["requests"]:
        bindings = strict_json(request["bindings_json"])
        canonical(bindings)
        if not isinstance(bindings, dict) or any(
            not isinstance(value, dict) for value in bindings.values()
        ):
            raise ValueError("Request bindings must contain objects for each location")
    for guard in plan["preservations"]:
        identities = strict_json(guard["source_identity_json"])
        canonical(identities)
        if not isinstance(identities, dict) or not identities:
            raise ValueError("Preservation identities must be a nonempty JSON object")
    issues = preservation_issues(plan)
    canonical(task)
    positions = {r["id"]: position for position, r in enumerate(plan["requests"])}
    for group in ("requests", "checks", "requirements", "preservations"):
        identities = [item["id"] for item in plan[group]]
        if len(set(identities)) != len(identities) or any(not identity for identity in identities):
            issues.append(group + " must have unique, nonempty IDs")
    for requirement in plan["requirements"]:
        if requirement["coverage"] == "observable" and not requirement["check_ids"]:
            issues.append("Observable requirements must name executable checks")
    for request in plan["requests"]:
        # Only this non-mutating POST has a reviewed observation purpose.
        if (
            not request["operation"].startswith("GET ")
            and request["operation"] != "POST /v1/invoices/create_preview"
        ):
            issues.append("Operation has no reviewed read-only execution policy")
        issues += audit_request(index, request, task, {})["issues"]
        issues += audit_pagination(index, request)
        bindings = strict_json(request["bindings_json"])
        canonical(bindings)
        for value in expression_values(bindings):
            if value.startswith("$response."):
                source = value[len("$response.") :].partition("/")[0]
                if source not in positions or positions[source] >= positions[request["id"]]:
                    issues.append("Response dependency must name an earlier request")
    for check in plan["checks"]:
        issues += audit_check(index, check, requests.get(check["request_id"]))["issues"]
        for field in ("selector_json", "value_json"):
            value = strict_json(check[field])
            canonical(value)
            for expression in expression_values(value):
                if expression.startswith("$response."):
                    issues.append("Checks support context bindings, not response expressions")
                elif expression.startswith("$context."):
                    from ternela.expressions import bind

                    try:
                        bind(expression, task["context"])
                    except (KeyError, TypeError, IndexError, ValueError):
                        issues.append("Check references missing context")
    if issues:
        raise AdmissionError("Plan admission failed: " + "; ".join(dict.fromkeys(issues)))
    pins = json.loads(source_path("sources/manifest.json").read_text())
    validation_files = [
        *SOURCE_FILES,
        *[
            "ternela/" + name + ".py"
            for name in (
                "watches",
                "observations",
                "repository",
                "contracts",
                "errors",
                "database",
                "migrations",
                "evaluation",
                "expressions",
                "guards",
                "schema",
                "plan_schema",
                "plan_audit",
                "storage",
                "paths",
                "specification",
                "pinned_sources",
                "sources",
                "validation",
            )
        ],
    ]
    validator_code = {name: sha((PROJECT / name).read_bytes()) for name in validation_files}
    return {
        "pinned_source": getattr(index, "provenance", None)
        or next(p for p in pins if p["provider"] == task["provider"]),
        "supplements": getattr(index, "supplements", []),
        "validator_sha256": sha(canonical(validator_code)),
        "validator_code": validator_code,
    }


def expression_values(value):
    """Yield expression strings without interpreting arbitrary user code."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from expression_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from expression_values(child)
