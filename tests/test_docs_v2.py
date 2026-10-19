# SPDX-License-Identifier: Apache-2.0
import copy
import json
import unittest

from docs_lab.v2.runtime import bind_observations, check_one, execute
from docs_lab.v2.retrieval import tree


def paging(mode="none", items="", **updates):
    return dict({"mode": mode, "items_pointer": items, "cursor_parameter": "", "next_cursor_pointer": "",
                 "has_more_pointer": "", "cursor_item_pointer": "", "max_pages": 5}, **updates)


def request(pagination):
    return {"operation": "GET /objects", "bindings_json": "{}", "pagination": pagination}


def response(body, link=None, headers_complete=True, status=200):
    return {"http_status": status, "body": body, "headers": {"link": link} if link else {},
            "headers_complete": headers_complete, "url": "https://example.test/objects"}


def check(mode="count", value="0"):
    return {"collection_pointer": "", "selector_json": "{}", "select_mode": mode,
            "order_by": "", "field_pointer": "", "op": "eq", "value_json": value}


class V2Tests(unittest.TestCase):
    def play(self, pages, configuration):
        calls = []
        def transport(call):
            calls.append(call)
            return copy.deepcopy(pages[len(calls)-1])
        return execute(request(configuration), {}, transport), calls

    def test_link_pages_aggregate_and_observe_next_url(self):
        result, calls = self.play([response([1], '<https://example.test/objects?page=2>; rel="next"'), response([2])], paging("link"))
        self.assertEqual(result["body"], [1,2])
        self.assertTrue(result["collection_complete"])
        self.assertEqual(calls[1]["next_url"], "https://example.test/objects?page=2")

    def test_empty_first_page_with_next_is_not_absence(self):
        result, _ = self.play([response([], '<https://example.test/objects?page=2>; rel="next"'), response([2])], paging("link"))
        self.assertEqual(check_one(check(), result, {}), "false")
        empty, _ = self.play([response([])], paging("link"))
        self.assertEqual(check_one(check(), empty, {}), "true")

    def test_missing_headers_budget_and_loops_remain_unknown(self):
        next_link = '<https://example.test/objects?page=2>; rel="next"'
        for pages, config in (([response([], headers_complete=False)], paging("link")),
                              ([response([], next_link)], paging("link", max_pages=1)),
                              ([response([], next_link),response([],next_link)], paging("link"))):
            result, _ = self.play(pages, config)
            self.assertFalse(result["collection_complete"])
            self.assertEqual(check_one(check(), result, {}), "unknown")

    def test_cursor_last_item_and_terminal_metadata(self):
        config = paging("cursor", "/data", cursor_parameter="starting_after", has_more_pointer="/has_more", cursor_item_pointer="/id")
        result, calls = self.play([response({"data":[{"id":"one"}],"has_more":True}), response({"data":[{"id":"two"}],"has_more":False})], config)
        self.assertEqual(calls[1]["bindings"]["query"]["starting_after"], "one")
        self.assertEqual(len(result["body"]["data"]),2)
        self.assertIs(result["body"]["has_more"],False)
        self.assertTrue(result["collection_complete"])

    def test_cursor_token_round_trip_and_primitive_members(self):
        config = paging("cursor", "/members", cursor_parameter="cursor", next_cursor_pointer="/response_metadata/next_cursor")
        result, calls = self.play([response({"members":["other"],"response_metadata":{"next_cursor":"token2"}}), response({"members":["target"],"response_metadata":{"next_cursor":""}})], config)
        self.assertEqual(calls[1]["bindings"]["query"]["cursor"],"token2")
        probe = check("any", '"target"'); probe["collection_pointer"]="/members"
        self.assertEqual(check_one(probe,result,{}),"true")

    def test_missing_cursor_cannot_prove_absence(self):
        config = paging("cursor","/members",cursor_parameter="cursor",next_cursor_pointer="/response_metadata/next_cursor")
        result,_ = self.play([response({"members":[],"response_metadata":{}})],config)
        self.assertFalse(result["collection_complete"])

    def test_error_after_first_page_does_not_validate_partial_evidence(self):
        result,_ = self.play([response(["target"],'<https://example.test/objects?page=2>; rel="next"'),response({},status=429)],paging("link"))
        self.assertEqual(check_one(check("any",'"target"'),result,{}),"unknown")

    def test_cross_origin_continuation_is_not_followed(self):
        result,calls=self.play([response([], '<https://other.test/objects?page=2>; rel="next"')],paging("link"))
        self.assertEqual(len(calls),1)
        self.assertFalse(result["collection_complete"])

    def test_tied_latest_conflicts_remain_unknown(self):
        rows=[{"state":"success","at":1},{"state":"failure","at":1}]
        result,_=self.play([response(rows)],paging("link"))
        probe=check("latest",'"success"');probe.update(field_pointer="/state",order_by="/at")
        self.assertEqual(check_one(probe,result,{}),"unknown")

    def test_schema_tree_preserves_every_child_name_at_boundary(self):
        schema={"type":"object","properties":{f"field{i}":{"type":"string"} for i in range(400)}}
        rendered=tree({},schema,max_depth=0)
        self.assertEqual(set(rendered["properties"]),set(schema["properties"]))
        self.assertEqual(rendered["properties"]["field399"]["expand_at"],"/field399")

    def test_dependency_values_preserve_types_and_fail_closed(self):
        source={"read":{"collection_complete":True,"body":{"quantity":5}}}
        self.assertEqual(bind_observations({"body":{"quantity":"$response.read/quantity"}},{},source),{"body":{"quantity":5}})
        with self.assertRaises(KeyError):bind_observations("$response.absent/quantity",{},source)
        source["read"]["collection_complete"]=False
        with self.assertRaises(ValueError):bind_observations("$response.read/quantity",{},source)


if __name__ == "__main__":
    unittest.main()
