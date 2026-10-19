# SPDX-License-Identifier: Apache-2.0
"""Trust-boundary tests for model-generated plans and provider evidence."""

import copy
import unittest

from observation_lab.runtime import compile_plan, evaluate


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "x", "tools": [{"id": "quote", "cost_units": 2, "fields": {
            "amount": {"type": "number"}, "operation": {"type": "string"},
            "done": {"type": "boolean"}}}]}
        self.plan = {"task_id": "x", "checks": [
            {"id": "budget", "phase": "before", "tool": "quote", "field": "amount", "op": "lte",
             "value": 500, "max_age_seconds": 60, "reason": "Budget"},
            {"id": "identity", "phase": "after", "tool": "quote", "field": "operation", "op": "eq",
             "value": "op1", "max_age_seconds": 60, "reason": "Bind confirmation"},
            {"id": "receipt", "phase": "after", "tool": "quote", "field": "done", "op": "eq",
             "value": True, "max_age_seconds": 60, "reason": "Outcome"}],
            "requirements": [{"text": "Budget", "check_ids": ["budget"]}], "unresolved": [],
            "poll_interval_seconds": 30, "receipt_timeout_seconds": 600}
        self.evidence = {"quote": {"data": {"amount": 450, "operation": "op1", "done": True},
                                   "observed_at": 1000, "available": True}}

    def test_missing_and_wrong_types_fail_closed(self):
        for value in (None, True, "450", float("nan"), float("inf")):
            with self.subTest(value=value):
                self.evidence["quote"]["data"]["amount"] = value
                self.assertEqual(evaluate(self.plan, self.task, self.evidence)["status"], "unknown")
        self.evidence["quote"]["data"].pop("amount")
        self.assertEqual(evaluate(self.plan, self.task, self.evidence)["status"], "unknown")

    def test_harmless_change_and_budget_boundary(self):
        for amount, status in ((480, "ready"), (500, "ready"), (501, "blocked")):
            self.evidence["quote"]["data"]["amount"] = amount
            self.assertEqual(evaluate(self.plan, self.task, self.evidence)["status"], status)

    def test_absent_expired_future_or_unavailable_source(self):
        self.assertEqual(evaluate(self.plan, self.task, {})["status"], "unknown")
        for timestamp in (939, 1001, None, True, float("nan")):
            self.evidence["quote"]["observed_at"] = timestamp
            self.assertEqual(evaluate(self.plan, self.task, self.evidence)["status"], "unknown")
        self.evidence["quote"]["observed_at"] = 1000
        self.evidence["quote"]["available"] = False
        self.assertEqual(evaluate(self.plan, self.task, self.evidence)["status"], "unknown")

    def test_malformed_connector_envelopes_cannot_crash_or_authorize(self):
        for envelope in ("broken", [], 1, {"data": None}, {"data": []},
                         {"data": {"amount": 450}, "observed_at": 1000, "available": "false"}):
            self.assertEqual(evaluate(self.plan, self.task, {"quote": envelope})["status"], "unknown")

    def test_invalid_clocks_rejected(self):
        for now in (float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                evaluate(self.plan, self.task, self.evidence, now=now)
        for elapsed in (-1, float("nan"), True):
            with self.assertRaises(ValueError):
                evaluate(self.plan, self.task, self.evidence, elapsed_seconds=elapsed)

    def test_wrong_operation_cannot_confirm_and_deadline(self):
        self.evidence["quote"]["data"]["operation"] = "op_other"
        self.assertEqual(evaluate(self.plan, self.task, self.evidence, "after")["status"], "pending")
        self.assertEqual(evaluate(self.plan, self.task, self.evidence, "after", elapsed_seconds=600)["status"], "overdue")
        self.evidence["quote"]["data"]["operation"] = "op1"
        self.assertEqual(evaluate(self.plan, self.task, self.evidence, "after", elapsed_seconds=650)["status"], "complete")

    def test_untrusted_plan_rejects_code_extra_fields_and_unknown_tools(self):
        for key, value in (("tool", "os.system('bad')"), ("field", "__class__"), ("op", "eval"), ("value", True)):
            plan = copy.deepcopy(self.plan)
            plan["checks"][0][key] = value
            with self.assertRaises(ValueError):
                compile_plan(plan, self.task)
        plan = copy.deepcopy(self.plan)
        plan["python"] = "print('bad')"
        with self.assertRaises(ValueError):
            compile_plan(plan, self.task)

    def test_plan_identity_unique_check_ids_and_requirement_references(self):
        for change in (lambda p: p.update(task_id="other"),
                       lambda p: p["checks"][1].update(id="budget"),
                       lambda p: p["requirements"][0].update(check_ids=["invented"])):
            plan = copy.deepcopy(self.plan)
            change(plan)
            with self.assertRaises(ValueError):
                compile_plan(plan, self.task)

    def test_empty_checks_cannot_vacuously_authorize_or_complete(self):
        self.plan["checks"] = []
        self.plan["requirements"] = []
        with self.assertRaises(ValueError):
            compile_plan(self.plan, self.task)
        self.plan["unresolved"] = ["No outcome observer exists"]
        self.assertEqual(evaluate(self.plan, self.task, self.evidence)["status"], "unmonitorable")

    def test_numeric_limits_and_nonfinite_values_rejected(self):
        for key, value in (("poll_interval_seconds", 0), ("receipt_timeout_seconds", 601),
                           ("poll_interval_seconds", True)):
            plan = copy.deepcopy(self.plan)
            plan[key] = value
            with self.assertRaises(ValueError):
                compile_plan(plan, self.task)
        self.plan["checks"][0]["value"] = float("nan")
        with self.assertRaises(ValueError):
            compile_plan(self.plan, self.task)

    def test_checks_share_one_tool_read_per_evaluation(self):
        result = evaluate(self.plan, self.task, self.evidence, "after")
        self.assertEqual((result["status"], result["tool_reads"], result["cost_units"]), ("complete", 1, 2))

    def test_false_condition_blocks_even_if_other_evidence_unavailable(self):
        extra = copy.deepcopy(self.plan["checks"][0])
        extra.update(id="missing", field="done", op="eq", value=True)
        self.plan["checks"].append(extra)
        self.evidence["quote"]["data"].pop("done")
        self.evidence["quote"]["data"]["amount"] = 501
        self.assertEqual(evaluate(self.plan, self.task, self.evidence)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
