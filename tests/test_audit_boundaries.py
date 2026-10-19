# SPDX-License-Identifier: Apache-2.0
"""Regression cases found during the release audit, independent of provider APIs."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from docs_lab.runtime import compare
from docs_lab.source import pointer
from docs_lab.v2.runtime import check_details, check_one, execute
from expansion_lab.storage import Store, write_json
from observation_lab.schema import validate_shape
from ternela import rebrand
from workbench.client import ObservationClient
from workbench.engine import observe
from workbench.live import collect
from workbench.server import strict_json


class IntegrityTests(unittest.TestCase):
    def test_nonstandard_or_ambiguous_json_is_rejected(self):
        for raw in (
            '{"a":1,"a":2}',
            '{"n":NaN}',
            '{"n":Infinity}',
            '{"n":1e400}',
            "[" * 66 + "0" + "]" * 66,
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                strict_json(raw)

    def test_json_pointer_does_not_accept_python_negative_indices(self):
        for path in ("/-1", "/01", "/+1", "/bad~escape"):
            with self.subTest(path=path), self.assertRaises(KeyError):
                pointer(["wrong", "target"], path)
        self.assertEqual(pointer({"a/b": {"~": 3}}, "/a~1b/~0"), 3)

    def test_nonfinite_numbers_cannot_satisfy_not_equal(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                compare(value, "ne", 0)

    def test_schema_minimum_batch_size_is_enforced(self):
        schema = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2}
        with self.assertRaises(ValueError):
            validate_shape([], schema)

    def test_parallel_manifest_writes_leave_one_complete_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "nested/manifest.json"
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(
                    pool.map(
                        lambda n: write_json(target, {"revision": n, "payload": "x" * 2000}),
                        range(32),
                    )
                )
            result = json.loads(target.read_text())
            self.assertIn(result["revision"], range(32))
            self.assertEqual(result["payload"], "x" * 2000)
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_store_rejects_nonfinite_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                Store(temporary).put({"value": float("nan")})

    def test_client_rejects_non_origin_base_urls_before_network(self):
        for base in (
            "https://localhost:8765",
            "http://localhost:8765/path",
            "http://user@localhost:8765",
            "http://localhost:8765?x=1",
            "http://example.test",
        ):
            with self.subTest(base=base), self.assertRaises(ValueError):
                ObservationClient(base)

    def test_rebrand_preserves_namespace_and_can_be_repeated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            brand_path = root / "brand.json"
            brand = {"name": "Ternela", "tagline": "Wait reliably.", "repository": "owner/ternela"}
            write_json(brand_path, brand)
            text = "Ternela · Wait reliably. https://github.com/owner/ternela\nfrom ternela import Client\n"
            for relative in rebrand.DOCUMENTS:
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text)
            with (
                patch.object(rebrand, "PROJECT", root),
                patch.object(rebrand, "BRAND_PATH", brand_path),
            ):
                rebrand.update("New Ternela", "owner/new-name", "Ternela keeps watch.")
                self.assertIn(
                    "New Ternela · Ternela keeps watch.", (root / "README.md").read_text()
                )
                rebrand.update("Later Name", "owner/later-name", "Wait well.")
            result = (root / "README.md").read_text()
            self.assertIn("Later Name · Wait well. https://github.com/owner/later-name", result)
            self.assertIn("from ternela import Client", result)
            self.assertNotIn("New Name", result)


class PaginationAuditTests(unittest.TestCase):
    def test_failed_live_read_becomes_unknown_evidence(self):
        import subprocess

        plan = {
            "requests": [
                {
                    "id": "repo",
                    "operation": "GET /repos/{owner}/{repo}",
                    "bindings_json": '{"path":{"owner":"$context.owner","repo":"$context.repo"}}',
                    "pagination": {
                        "mode": "none",
                        "items_pointer": "",
                        "cursor_parameter": "",
                        "next_cursor_pointer": "",
                        "has_more_pointer": "",
                        "cursor_item_pointer": "",
                        "max_pages": 1,
                    },
                }
            ],
            "checks": [
                {
                    "id": "c",
                    "phase": "after",
                    "request_id": "repo",
                    "collection_pointer": "",
                    "selector_json": "{}",
                    "select_mode": "single",
                    "field_pointer": "/archived",
                    "order_by": "",
                    "op": "eq",
                    "value_json": "false",
                }
            ],
            "requirements": [],
            "preservations": [],
        }
        context = {"owner": "cli", "repo": "cli"}
        with patch(
            "workbench.live.subprocess.run", side_effect=subprocess.TimeoutExpired("curl", 15)
        ):
            snapshots, _ = collect(plan, context, "after")
        result, _ = observe(plan, context, snapshots, "after")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(snapshots["repo"][0]["http_status"], 0)

    def test_latest_dates_compare_instants_instead_of_timestamp_spelling(self):
        check = {
            "collection_pointer": "",
            "selector_json": "{}",
            "select_mode": "latest",
            "order_by": "/at",
            "field_pointer": "/state",
            "op": "eq",
            "value_json": '"success"',
        }
        envelope = {
            "http_status": 200,
            "collection_complete": True,
            "completed_pointer": "",
            "body": [
                {"at": "2026-10-19T10:00:00+02:00", "state": "success"},
                {"at": "2026-10-19T09:00:00Z", "state": "failure"},
            ],
        }
        self.assertEqual(check_one(check, envelope, {}), "false")
        self.assertEqual(check_details(check, envelope, {})["actual_json"], '"failure"')

    def test_display_preserves_large_exact_integer_and_count_zero(self):
        check = {
            "collection_pointer": "",
            "selector_json": "{}",
            "select_mode": "single",
            "order_by": "",
            "field_pointer": "/amount",
            "op": "eq",
            "value_json": "9007199254740993",
        }
        envelope = {
            "http_status": 200,
            "collection_complete": True,
            "completed_pointer": "",
            "body": {"amount": 9007199254740993},
        }
        self.assertEqual(check_one(check, envelope, {}), "true")
        self.assertEqual(check_details(check, envelope, {})["actual_json"], "9007199254740993")
        check.update(select_mode="count", field_pointer="", value_json="0")
        envelope["body"] = []
        self.assertEqual(check_details(check, envelope, {})["actual_json"], "0")

    def play(self, headers):
        request = {
            "operation": "GET /objects",
            "bindings_json": "{}",
            "pagination": {
                "mode": "link",
                "items_pointer": "",
                "cursor_parameter": "",
                "next_cursor_pointer": "",
                "has_more_pointer": "",
                "cursor_item_pointer": "",
                "max_pages": 3,
            },
        }
        calls = []

        def transport(call):
            calls.append(call)
            return {
                "http_status": 200,
                "body": [],
                "headers": headers,
                "headers_complete": True,
                "url": "https://example.test/objects",
            }

        return execute(request, {}, transport), calls

    def test_same_origin_different_resource_cannot_prove_absence(self):
        result, calls = self.play({"link": '<https://example.test/other?page=2>; rel="next"'})
        self.assertEqual(len(calls), 1)
        self.assertFalse(result["collection_complete"])

    def test_malformed_headers_are_incomplete_instead_of_crashing(self):
        for headers in (None, [], {"link": None}, {"link": "broken metadata"}):
            with self.subTest(headers=headers):
                result, _ = self.play(headers)
                self.assertFalse(result["collection_complete"])

    def test_multiple_relations_include_next_and_stop_on_repetition(self):
        result, calls = self.play({"link": '<?page=2>; rel="next alternate"'})
        self.assertEqual(len(calls), 2)
        self.assertFalse(result["collection_complete"])


if __name__ == "__main__":
    unittest.main()
