# SPDX-License-Identifier: Apache-2.0
"""Current compiler entry point: expansion plus phase-safe contract validation."""
from pathlib import Path
from expansion_lab import run as original
from expansion_lab.evidence import Evidence
from expansion_lab.storage import PROJECT, sha
from docs_lab.audit import audit_check
from .runtime import preservation_issues

CONTRACT = """
PHASE-SAFE BUSINESS CONTRACT:
Existing-item price-only invoice previews occur BEFORE a subscription change. Every check
of that preview, including currency and budget, must gate BEFORE readiness. Requirements
must reference checks in their own phase. Actual final-invoice checks remain AFTER.
Any failed typed preservation guard makes its declared phase unknown, regardless of other
true checks. Never infer readiness merely because a source quantity exists.
Unsupported schema pointers are public evidence gaps. Do not invent field checks when an
ambiguous vendor schema prevents establishing them; retain explicit partial coverage.
"""


class HardenedEvidence(Evidence):
    def expand(self, batch, iteration):
        result = super().expand(batch, iteration)
        for plan in batch["plans"]:
            identity = plan["task_id"]
            index = self.indexes[self.tasks[identity]["provider"]]
            requests = {r["id"]: r for r in plan["requests"]}
            issues = preservation_issues(plan)
            for check in plan["checks"]:
                issues += audit_check(index, check, requests.get(check["request_id"]))["issues"]
            if issues:
                result["public_validation_feedback"][identity] = sorted(set(result["public_validation_feedback"].get(identity, []) + issues))
                result["events"].append({"kind": "contract_validation", "task_id": identity, "issues": issues})
        return result


def main():
    old_manifest, old_infer = original.code_manifest, original.infer
    def manifest():
        hashes = old_manifest()
        hashes.update({str(p.relative_to(PROJECT)): sha(p.read_bytes()) for p in Path(__file__).parent.rglob("*.py")})
        return hashes
    def infer(payload, stage, schema, instructions, *args):
        return old_infer(payload, stage, schema, instructions + CONTRACT, *args)
    original.Evidence = HardenedEvidence
    original.code_manifest = manifest
    original.infer = infer
    original.main()


if __name__ == "__main__":
    main()
