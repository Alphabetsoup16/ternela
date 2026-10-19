# SPDX-License-Identifier: Apache-2.0
import gzip
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from docs_lab.v2.grade import envelope
from expansion_lab.evidence import Evidence
from expansion_lab.runtime import preservation_issues, run_plan
from expansion_lab.source import neighbors
from expansion_lab.storage import Store


def paging():
    return dict(
        mode="none",
        items_pointer="",
        cursor_parameter="",
        next_cursor_pointer="",
        has_more_pointer="",
        cursor_item_pointer="",
        max_pages=1,
    )


def plan():
    return {
        "task_id": "q",
        "requests": [
            {
                "id": "item",
                "operation": "GET /v1/subscription_items/{item}",
                "bindings_json": '{"path":{"item":"$context.item"}}',
                "pagination": paging(),
            },
            {
                "id": "preview",
                "operation": "POST /v1/invoices/create_preview",
                "bindings_json": json.dumps(
                    {
                        "body": {
                            "subscription": "$context.subscription",
                            "subscription_details": {
                                "items": [
                                    {
                                        "id": "$context.item",
                                        "price": "price_new",
                                        "quantity": "$response.item/quantity",
                                    }
                                ]
                            },
                        }
                    }
                ),
                "pagination": paging(),
            },
        ],
        "checks": [
            {
                "id": "budget",
                "request_id": "preview",
                "phase": "before",
                "collection_pointer": "",
                "selector_json": "{}",
                "select_mode": "single",
                "order_by": "",
                "field_pointer": "/amount_due",
                "op": "lte",
                "value_json": "100",
            }
        ],
        "requirements": [],
        "preservations": [
            {
                "id": "quantity",
                "phase": "before",
                "source_request_id": "item",
                "source_pointer": "/quantity",
                "source_identity_json": '{"/id":"$context.item","/subscription":"$context.subscription"}',
                "target_request_id": "preview",
                "target_pointer": "/body/subscription_details/items/0/quantity",
                "value_type": "integer",
                "reason": "Preserve",
            }
        ],
        "retrieval_needs": [],
    }


class PreservationTests(unittest.TestCase):
    def play(self, p, value=5, patch=None, status=200):
        calls = []

        def transport(call):
            calls.append(call)
            if call["operation"].startswith("GET "):
                return envelope(
                    dict({"id": "si", "subscription": "sub", "quantity": value}, **(patch or {})),
                    status=status,
                )
            return envelope({"amount_due": 90})

        result, responses = run_plan(p, {"item": "si", "subscription": "sub"}, transport, "before")
        return result, calls, responses

    def test_copies_observed_quantities_including_zero(self):
        for quantity in [0, 1, 7, 13]:
            result, calls, _ = self.play(plan(), quantity)
            self.assertEqual(result["status"], "ready")
            self.assertEqual(
                calls[-1]["bindings"]["body"]["subscription_details"]["items"][0]["quantity"],
                quantity,
            )

    def test_rejects_string_boolean_and_missing_source_values(self):
        for quantity in ["5", True, None]:
            result, calls, _ = self.play(plan(), quantity)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(len(calls), 1)

    def test_wrong_item_or_subscription_never_reaches_preview(self):
        for patch in [{"id": "si_other"}, {"subscription": "sub_other"}]:
            result, calls, _ = self.play(plan(), patch=patch)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(len(calls), 1)

    def test_forbidden_source_never_reaches_preview(self):
        result, calls, _ = self.play(plan(), status=403)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(calls), 1)

    def test_literal_quantity_cannot_pass_by_coincidence(self):
        p = plan()
        bindings = json.loads(p["requests"][1]["bindings_json"])
        bindings["body"]["subscription_details"]["items"][0]["quantity"] = 5
        p["requests"][1]["bindings_json"] = json.dumps(bindings)
        result, calls, _ = self.play(p)
        self.assertEqual(result["status"], "unverifiable")
        self.assertEqual(calls, [])

    def test_omitted_guard_is_a_boundary_error(self):
        p = plan()
        p["preservations"] = []
        self.assertTrue(preservation_issues(p))

    def test_source_must_precede_target(self):
        p = plan()
        p["requests"].reverse()
        self.assertTrue(preservation_issues(p))

    def test_identity_conditions_are_required_not_just_source_value(self):
        p = plan()
        p["preservations"][0]["source_identity_json"] = "{}"
        self.assertTrue(preservation_issues(p))

    def test_malformed_identity_is_reported_as_a_contract_issue(self):
        p = plan()
        p["preservations"][0]["source_identity_json"] = "[]"
        self.assertTrue(preservation_issues(p))


class BlobTests(unittest.TestCase):
    def test_compresses_deduplicates_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            value = {"x": "repeat " * 10000}
            a, b = store.put(value), store.put(value)
            self.assertEqual(a, b)
            self.assertLess(a["gzip_bytes"], a["bytes"] // 10)
            self.assertEqual(store.get(a), value)
            self.assertEqual(len(list(Path(directory).rglob("*.gz"))), 1)

    def test_parallel_identical_blobs_are_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with ThreadPoolExecutor(max_workers=4) as pool:
                refs = list(pool.map(store.put, [{"repeat": "yes"}] * 30))
            self.assertTrue(all(store.get(ref) == {"repeat": "yes"} for ref in refs))
            self.assertEqual(len(list(Path(directory).rglob("*.gz"))), 1)

    def test_corruption_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            ref = store.put({"correct": True})
            path = next(Path(directory).rglob("*.gz"))
            path.write_bytes(gzip.compress(b"wrong"))
            with self.assertRaises(ValueError):
                store.get(ref)


class ExpansionTests(unittest.TestCase):
    def index(self):
        item = {"$ref": "#/components/schemas/Job"}
        spec = {
            "components": {
                "schemas": {
                    "Job": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "state": {"type": "string", "enum": ["pending", "done"]},
                        },
                    }
                }
            },
            "paths": {},
        }
        entries = []
        for method, path, schema in [
            ("get", "/jobs", {"type": "array", "items": item}),
            ("get", "/jobs/{job}", item),
            ("post", "/jobs", item),
        ]:
            operation = {
                "responses": {"200": {"content": {"application/json": {"schema": schema}}}}
            }
            entry = {
                "method": method,
                "path": path,
                "key": method.upper() + " " + path,
                "operation": operation,
                "item": {method: operation},
            }
            entries.append(entry)
        index = SimpleNamespace(
            provider="test", spec=spec, entries=entries, by_key={e["key"]: e for e in entries}
        )
        index.search = lambda query, count: [{"operation": "GET /jobs", "score": 1}]
        return index

    def test_resource_graph_finds_direct_read_not_mutation(self):
        hits = neighbors(self.index(), ["GET /jobs"])
        self.assertEqual([h["operation"] for h in hits], ["GET /jobs/{job}"])

    def test_used_field_triggers_targeted_expansion(self):
        e = Evidence(
            {"test": self.index()},
            [{"id": "t", "provider": "test", "task": "Wait for a job", "context": {}}],
            "adaptive",
        )
        e.select({"plans": [{"task_id": "t", "operations": ["GET /jobs"], "schema_reads": []}]})
        p = {
            "task_id": "t",
            "requests": [{"id": "jobs", "operation": "GET /jobs", "bindings_json": "{}"}],
            "checks": [
                {
                    "request_id": "jobs",
                    "collection_pointer": "",
                    "selector_json": "{}",
                    "field_pointer": "/state",
                    "order_by": "",
                }
            ],
            "requirements": [],
            "preservations": [],
            "retrieval_needs": [],
            "followup_queries": [],
        }
        result = e.expand({"plans": [p]}, 1)
        self.assertTrue(
            any(x["kind"] == "schema_read" and x["pointer"] == "/*/state" for x in result["events"])
        )
        branch = next(iter(e.reads.values()))
        self.assertTrue(branch["result"]["found"])
        self.assertEqual(branch["result"]["nodes"][0]["enum"], ["pending", "done"])

    def test_unobservable_external_fact_does_not_force_directory_fallback(self):
        e = Evidence(
            {"test": self.index()},
            [{"id": "t", "provider": "test", "task": "Prove an external event", "context": {}}],
            "adaptive",
        )
        p = {
            "task_id": "t",
            "requests": [],
            "checks": [],
            "requirements": [{"coverage": "unobservable"}],
            "preservations": [],
            "retrieval_needs": [{"kind": "external_fact", "query": "physical fact"}],
            "followup_queries": [],
        }
        result = e.expand({"plans": [p]}, 2)
        self.assertFalse(result["fallback_used"])
        self.assertEqual(result["events"], [])

    def test_fallback_is_bounded_and_visible(self):
        e = Evidence(
            {"test": self.index()},
            [{"id": "t", "provider": "test", "task": "Wait", "context": {}}],
            "adaptive",
        )
        e.selected["t"] = []
        p = {
            "task_id": "t",
            "requests": [],
            "checks": [],
            "requirements": [{"coverage": "partial"}],
            "preservations": [],
            "retrieval_needs": [{"kind": "operation", "query": "missing"}],
            "followup_queries": [],
        }
        first, second = e.expand({"plans": [p]}, 2), e.expand({"plans": [p]}, 2)
        self.assertEqual(
            sum(x["kind"] == "complete_directory_fallback" for x in first["events"]), 1
        )
        self.assertEqual(
            sum(x["kind"] == "complete_directory_fallback" for x in second["events"]), 0
        )


if __name__ == "__main__":
    unittest.main()
