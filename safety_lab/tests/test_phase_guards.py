# SPDX-License-Identifier: Apache-2.0
import json
import unittest
from types import SimpleNamespace

from docs_lab.v2.grade import envelope
from safety_lab.run import HardenedEvidence
from safety_lab.runtime import preservation_issues, run_plan


def fixture(preview=True, target_phase="before"):
    pagination = {
        "mode": "none",
        "items_pointer": "",
        "cursor_parameter": "",
        "next_cursor_pointer": "",
        "has_more_pointer": "",
        "cursor_item_pointer": "",
        "max_pages": 1,
    }
    body = {
        "subscription": "$context.subscription",
        "subscription_details": {
            "items": [
                {"id": "$context.item", "price": "p_new", "quantity": "$response.source/quantity"}
            ]
        },
    }

    def check(identity, request, phase, field, op, value):
        return {
            "id": identity,
            "request_id": request,
            "phase": phase,
            "collection_pointer": "",
            "selector_json": "{}",
            "select_mode": "single",
            "order_by": "",
            "field_pointer": field,
            "op": op,
            "value_json": value,
        }

    return {
        "requests": [
            {
                "id": "source",
                "operation": "GET /item",
                "bindings_json": "{}",
                "pagination": pagination,
            },
            {
                "id": "target",
                "operation": (
                    "POST /v1/invoices/create_preview" if preview else "GET /observation_preview"
                ),
                "bindings_json": json.dumps({"body": body}),
                "pagination": pagination,
            },
        ],
        "checks": [
            check("exists", "source", "before", "/quantity", "exists", "true"),
            check("budget", "target", target_phase, "/amount_due", "lte", "100"),
        ],
        "requirements": [],
        "preservations": [
            {
                "id": "quantity",
                "phase": "before",
                "source_request_id": "source",
                "source_pointer": "/quantity",
                "source_identity_json": '{"/id":"$context.item","/subscription":"$context.subscription"}',
                "target_request_id": "target",
                "target_pointer": "/body/subscription_details/items/0/quantity",
                "value_type": "integer",
                "reason": "Keep observed quantity",
            }
        ],
    }


class PhaseTests(unittest.TestCase):
    def play(self, p, quantity=5, amount=90, item="si"):
        calls = []

        def transport(call):
            calls.append(call)
            return (
                envelope({"id": item, "subscription": "sub", "quantity": quantity})
                if call["operation"] == "GET /item"
                else envelope({"amount_due": amount})
            )

        result, observed = run_plan(p, {"item": "si", "subscription": "sub"}, transport, "before")
        return result, calls

    def test_valid_preview_can_be_ready(self):
        self.assertEqual(self.play(fixture())[0]["status"], "ready")

    def test_over_budget_preview_is_blocked(self):
        self.assertEqual(self.play(fixture(), amount=101)[0]["status"], "blocked")

    def test_preview_checks_cannot_be_deferred_to_after(self):
        p = fixture(target_phase="after")
        result, calls = self.play(p)
        self.assertEqual(result["status"], "unverifiable")
        self.assertEqual(calls, [])

    def test_generic_before_guard_failure_overrides_other_true_checks(self):
        for value in ["5", True]:
            p = fixture(preview=False, target_phase="after")
            result, calls = self.play(p, quantity=value)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(len(calls), 1)

    def test_wrong_identity_guard_failure_is_unknown(self):
        result, calls = self.play(fixture(preview=False, target_phase="after"), item="other")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(calls), 1)

    def test_requirements_cannot_reference_a_different_phase(self):
        p = fixture()
        p["requirements"] = [{"phase": "after", "check_ids": ["budget"]}]
        self.assertTrue(preservation_issues(p))

    def test_preview_needs_target_checks(self):
        p = fixture()
        p["checks"] = p["checks"][:1]
        self.assertTrue(preservation_issues(p))

    def test_zero_remains_a_valid_preserved_integer(self):
        result, calls = self.play(fixture(), quantity=0)
        self.assertEqual(result["status"], "ready")
        self.assertIs(
            type(calls[-1]["bindings"]["body"]["subscription_details"]["items"][0]["quantity"]), int
        )

    def test_hardened_expansion_surfaces_phase_feedback(self):
        p = fixture(target_phase="after")
        p.update(task_id="t", retrieval_needs=[], followup_queries=[])
        # With no check schema reads requested, source shape is irrelevant to
        # the extra phase validator. Use a tiny public index with no hidden tests.
        index = SimpleNamespace(
            provider="x", by_key={}, entries=[], spec={}, search=lambda query, count: []
        )
        e = HardenedEvidence(
            {"x": index},
            [{"id": "t", "provider": "x", "task": "Preview before a change", "context": {}}],
            "adaptive",
        )
        e.selected["t"] = []
        result = e.expand({"plans": [p]}, 1)
        self.assertTrue(any(x["kind"] == "contract_validation" for x in result["events"]))
        self.assertTrue(any("BEFORE" in x for x in result["public_validation_feedback"]["t"]))


if __name__ == "__main__":
    unittest.main()
