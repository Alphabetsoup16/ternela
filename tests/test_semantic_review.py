# SPDX-License-Identifier: Apache-2.0
import json
import os
import unittest
from unittest.mock import patch

from ternela.semantic_review import DIMENSIONS, review_plan, review_request


class SemanticReviewTests(unittest.TestCase):
    task = {
        "task": "Wait for check run 42 on this exact commit to succeed",
        "provider": "github",
        "context": {"id": 42},
    }
    plan = {"checks": [], "requirements": []}

    def response(self):
        return {
            "model": "jev-test-fixture",
            "answers": {
                key: {
                    "type": "choice",
                    "choice": "mismatch",
                    "confidence": 0.9,
                    "probabilities": {"matches": 0.05, "mismatch": 0.9, "unclear": 0.05},
                }
                for key in DIMENSIONS
            },
        }

    def test_missing_key_does_not_send_a_request(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("ternela.semantic_review.post") as request,
        ):
            with self.assertRaisesRegex(RuntimeError, "TYPESAFE_API_KEY"):
                review_plan(self.task, self.plan)
            request.assert_not_called()

    def test_independent_dimensions_include_no_match_and_ambiguity(self):
        request = review_request(self.task, self.plan)
        self.assertEqual(set(request["questions"]), set(DIMENSIONS))
        self.assertTrue(
            all(
                set(item["criteria"]) == {"matches", "mismatch", "unclear"}
                for item in request["questions"].values()
            )
        )

    def test_exact_large_integer_is_preserved_as_text(self):
        task = dict(self.task, context={"id": 9007199254740993})
        self.assertEqual(
            review_request(task, self.plan)["state"]["task"]["context"]["id"],
            {"exact_integer": "9007199254740993"},
        )

    def test_report_is_advisory_and_does_not_contain_secret(self):
        with patch("ternela.semantic_review.post", return_value=self.response()):
            report = review_plan(self.task, self.plan, api_key="not-a-real-key")
        self.assertIn("advisory", report["scope"])
        self.assertNotIn("not-a-real-key", json.dumps(report))
        self.assertEqual(report["answers"]["condition_coverage"]["choice"], "mismatch")

    def test_missing_judgment_and_nonfinite_probabilities_are_rejected(self):
        for change in ("missing", "nonfinite", "wrong_choice", "bad_sum"):
            response = self.response()
            answer = response["answers"]["resource_scope"]
            if change == "missing":
                del response["answers"]["phase_meaning"]
            elif change == "nonfinite":
                answer["confidence"] = float("nan")
            elif change == "wrong_choice":
                answer["choice"] = "matches"
            else:
                answer["probabilities"]["unclear"] = 0.9
            with patch("ternela.semantic_review.post", return_value=response):
                with self.assertRaises(ValueError):
                    review_plan(self.task, self.plan, api_key="not-a-real-key")
