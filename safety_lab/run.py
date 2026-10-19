# SPDX-License-Identifier: Apache-2.0
"""Add public business-phase validation feedback to retrieval expansion."""

from docs_lab.audit import audit_check, audit_pagination, audit_request
from expansion_lab.evidence import Evidence
from ternela.validation import admit

from .runtime import preservation_issues

CONTRACT = """
PHASE-SAFE BUSINESS CONTRACT:
Existing-item price-only invoice previews occur BEFORE a subscription change.
Every preview check, including currency and budget, must gate BEFORE readiness.
Requirements reference checks in their own phase. Final-invoice checks remain AFTER.
Failed typed preservation guards make their phase unknown. Unsupported schema
pointers remain explicit partial coverage rather than invented field checks.
SUPPLEMENTARY EVIDENCE:
Optional response shapes may come from separately hashed official JSON examples.
An example is not a required-field or semantic guarantee. Missing fields remain unknown.
"""


class HardenedEvidence(Evidence):
    def expand(self, batch, iteration):
        result = super().expand(batch, iteration)
        for plan in batch["plans"]:
            identity = plan["task_id"]
            index = self.indexes[self.tasks[identity]["provider"]]
            requests = {r["id"]: r for r in plan["requests"]}
            issues = preservation_issues(plan)
            try:
                admit(plan, self.tasks[identity], self.indexes)
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                issues.append(str(exc))
            for request in plan["requests"]:
                issues += audit_request(index, request, self.tasks[identity], {})["issues"]
                issues += audit_pagination(index, request)
            for check in plan["checks"]:
                issues += audit_check(index, check, requests.get(check["request_id"]))["issues"]
            if issues:
                result["public_validation_feedback"][identity] = sorted(
                    set(result["public_validation_feedback"].get(identity, []) + issues)
                )
                result["events"].append(
                    {"kind": "contract_validation", "task_id": identity, "issues": issues}
                )
        return result
