# SPDX-License-Identifier: Apache-2.0
"""Evaluator mechanics use explicit mocks; these tests never measure Jev's quality."""

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import PROJECT, Store
from ternela.semantic_eval import evaluate, load_cases, save_report, summarize
from ternela.semantic_review import DIMENSIONS


def fixture_review(expected):
    return {
        "model": "jev-mocked-for-tests",
        "answers": {key: {"choice": expected.get(key, "unclear")} for key in DIMENSIONS},
    }


class SemanticEvaluationTests(unittest.TestCase):
    def test_frozen_corpus_has_explicit_meaning_errors_and_ambiguity(self):
        corpus, manifest = load_cases()
        self.assertEqual(len(corpus["cases"]), 14)
        self.assertEqual(len(manifest["raw_sha256"]), 64)
        self.assertEqual(
            {value for case in corpus["cases"] for value in case["expected"].values()},
            {"matches", "mismatch", "unclear"},
        )
        self.assertIn("Not blind", corpus["scope"])

    def test_modified_corpus_is_rejected_before_any_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "evals"
            root.mkdir()
            manifest = (PROJECT / "evals/semantic_cases.manifest.json").read_bytes()
            packed = (PROJECT / "evals/semantic_cases.json.gz").read_bytes()
            corpus = json.loads(gzip.decompress(packed))
            corpus["cases"][0]["task"]["task"] += " changed"
            (root / "semantic_cases.manifest.json").write_bytes(manifest)
            (root / "semantic_cases.json.gz").write_bytes(
                gzip.compress(json.dumps(corpus).encode())
            )
            with patch("ternela.semantic_eval.PROJECT", Path(directory)):
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    load_cases()

    def test_offline_and_missing_credentials_never_call_the_model(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("ternela.semantic_eval.review_plan") as model,
        ):
            self.assertEqual(evaluate()["reason"], "offline_validation_only")
            result = evaluate(live=True)
            self.assertEqual(result["status"], "not_run")
            self.assertEqual(result["reason"], "missing_credentials")
            self.assertEqual(result["records"], [])
            self.assertNotIn("summary", result)
            model.assert_not_called()

    def test_confusion_matrix_distinguishes_false_matches_uncertainty_and_service_failure(self):
        records = [
            {
                "expected": {"resource_scope": "mismatch"},
                "review": fixture_review({"resource_scope": "matches"}),
            },
            {
                "expected": {"resource_scope": "matches"},
                "review": fixture_review({"resource_scope": "unclear"}),
            },
            {
                "expected": {"resource_scope": "unclear"},
                "review": fixture_review({"resource_scope": "unclear"}),
            },
            {"expected": {"resource_scope": "mismatch"}, "error": "review_failed"},
        ]
        summary = summarize(records)
        self.assertEqual(summary["judgments_scored"], 3)
        self.assertEqual(summary["correct"], 1)
        self.assertEqual(summary["false_matches"], 1)
        self.assertEqual(summary["unclear_answers"], 2)
        self.assertEqual(summary["service_failures"], 1)

    def test_one_service_failure_does_not_disappear_or_abort_the_remaining_cases(self):
        corpus, _ = load_cases()
        responses = [fixture_review(case["expected"]) for case in corpus["cases"]]
        responses[1] = RuntimeError("service unavailable")
        with (
            patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-only-key"}),
            patch("ternela.semantic_eval.review_plan", side_effect=responses) as model,
        ):
            result = evaluate(live=True, model="jev-test-explicit")
        self.assertEqual(model.call_count, 14)
        self.assertEqual(result["status"], "completed_with_failures")
        self.assertEqual(result["summary"]["service_failures"], 1)
        self.assertEqual(len(result["records"]), 14)
        self.assertEqual(result["requested_model"], "jev-test-explicit")
        self.assertNotIn("test-only-key", json.dumps(result))

    def test_saved_answers_are_compressed_verified_and_referenced(self):
        record = {
            "id": "fixture",
            "expected": {"resource_scope": "mismatch"},
            "review": fixture_review({"resource_scope": "mismatch"}),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = save_report({"status": "test_fixture", "records": [record]}, directory)
            manifest = json.loads(path.read_text())
            ref = manifest["records"][0]["ref"]
            self.assertNotIn("answers", path.read_text())
            self.assertEqual(Store(directory).get(ref), record)
            self.assertEqual(len(list((Path(directory) / "blobs").rglob("*.gz"))), 1)
