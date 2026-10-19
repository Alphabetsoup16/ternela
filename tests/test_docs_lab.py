# SPDX-License-Identifier: Apache-2.0
"""Boundaries needed when turning unfamiliar documentation into checks."""

import copy
import unittest

from docs_lab.audit import audit_check, audit_request, nodes_at
from docs_lab.runtime import check_one, evaluate
from docs_lab.source import Index, fields


class DocsLabTests(unittest.TestCase):
    def setUp(self):
        # Tiny protocol fixture independent of any provider-specific endpoint.
        self.spec = {"openapi": "3.0.0", "paths": {"/jobs/{job}": {"get": {
            "summary": "Read job", "parameters": [{"in": "path", "name": "job", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Result"}}}}}}}},
            "components": {"schemas": {"Result": {"type": "object", "properties": {
                "state": {"type": "string"}, "records": {"type": "array", "items": {"type": "object", "properties": {
                    "state": {"type": "string"}, "created_at": {"type": "string"}}}}}}}}}
        self.index = object.__new__(Index)
        self.index.provider = "test"
        self.index.spec = self.spec
        item = self.spec["paths"]["/jobs/{job}"]
        self.index.by_key = {"GET /jobs/{job}": {"path": "/jobs/{job}", "method": "get", "item": item, "operation": item["get"]}}
        self.request = {"id": "r", "operation": "GET /jobs/{job}", "bindings_json": '{"path":{"job":"$context.job"}}',
                        "auth_notes": "Read access", "purpose": "Read", "poll_interval_seconds": 60}
        self.task = {"provider": "test", "context": {"job": 7}}
        self.check = {"id": "c", "request_id": "r", "phase": "after", "collection_pointer": "/records", "selector_json": "{}",
                      "select_mode": "latest", "order_by": "/created_at", "field_pointer": "/state", "op": "eq", "value_json": '"success"', "reason": "Latest state"}

    def test_latest_status_cannot_be_satisfied_by_old_success(self):
        rows = [{"state": "success", "created_at": "2026-10-17T01:00:00Z"}, {"state": "failure", "created_at": "2026-10-18T01:00:00Z"}]
        for values in (rows, list(reversed(rows))):
            self.assertEqual(check_one(self.check, {"http_status": 200, "body": {"records": values}}, {}), "false")

    def test_forbidden_slack_errors_missing_and_empty_are_unknown(self):
        for response in (None, "bad", {"http_status": 403, "body": {}}, {"http_status": 200, "body": {"ok": False}},
                         {"http_status": 200, "body": {}}, {"http_status": 200, "body": {"records": []}}):
            self.assertEqual(check_one(self.check, response, {}), "unknown")

    def test_conflicting_latest_timestamps_are_unknown(self):
        rows = [{"state": "success", "created_at": "2026-10-18T01:00:00Z"},
                {"state": "failure", "created_at": "2026-10-18T01:00:00Z"}]
        for values in (rows, list(reversed(rows))):
            self.assertEqual(check_one(self.check, {"http_status": 200, "body": {"records": values}}, {}), "unknown")

    def test_ref_and_array_pointer_audit_rejects_invented_field(self):
        self.assertFalse(audit_check(self.index, self.check, self.request)["issues"])
        self.check["field_pointer"] = "/made_up"
        self.assertTrue(audit_check(self.index, self.check, self.request)["issues"])

    def test_missing_path_binding_and_unknown_parameter_rejected(self):
        self.request["bindings_json"] = '{"query":{"made_up":1}}'
        result = audit_request(self.index, self.request, self.task, {"test::GET /jobs/{job}"})
        self.assertGreaterEqual(len(result["issues"]), 2)

    def test_context_binding_is_data_and_unknown_reference_fails(self):
        good = audit_request(self.index, self.request, self.task, {"test::GET /jobs/{job}"})
        self.assertFalse(good["issues"])
        self.assertEqual(good["resolved_bindings"]["path"]["job"], 7)
        self.request["bindings_json"] = '{"path":{"job":"$context.missing"}}'
        self.assertTrue(audit_request(self.index, self.request, self.task, set())["issues"])

    def test_phase_coverage_gap_does_not_disable_other_phase(self):
        before = copy.deepcopy(self.check)
        before.update(id="before", phase="before", collection_pointer="", field_pointer="/state", select_mode="single", order_by="")
        plan = {"checks": [before, self.check], "requirements": [
            {"phase": "before", "coverage": "observable"}, {"phase": "after", "coverage": "unobservable"}]}
        response = {"r": {"http_status": 200, "body": {"state": "success"}}}
        self.assertEqual(evaluate(plan, response, {}, "before")["status"], "ready")
        self.assertEqual(evaluate(plan, response, {}, "after")["status"], "unverifiable")

    def test_schema_union_can_locate_real_field_without_inventing(self):
        schema = {"oneOf": [{"type": "null"}, {"type": "object", "properties": {"value": {"type": "number"}}}]}
        self.assertTrue(nodes_at(self.spec, schema, ["value"]))
        self.assertFalse(nodes_at(self.spec, schema, ["not_present"]))

    def test_recursive_schema_projection_is_bounded_and_marks_truncation(self):
        self.spec["components"]["schemas"]["Loop"] = {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Loop"}}}
        rows = fields(self.spec, {"$ref": "#/components/schemas/Loop"})
        self.assertLess(len(rows), 10)
        self.assertTrue(any("truncated" in row for row in rows))


if __name__ == "__main__":
    unittest.main()
