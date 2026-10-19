# SPDX-License-Identifier: Apache-2.0
"""Expand documentation from public evidence gaps, without test outcomes."""

import json

from docs_lab.audit import nodes_at, split_pointer
from docs_lab.source import response_schema
from docs_lab.v2.retrieval import branch, card, directory, root_names

from .runtime import preservation_issues
from .source import neighbors


def read(index, operation, section, pointer):
    try:
        return branch(index, operation, section, pointer)
    except (KeyError, ValueError, TypeError, IndexError):
        return {"found": False, "error": "Operation or schema pointer not found"}


def used_reads(index, plan):
    requests = {r["id"]: r for r in plan["requests"]}
    found = set()
    for check in plan["checks"]:
        request = requests.get(check["request_id"])
        if not request or request["operation"] not in index.by_key:
            continue
        op = request["operation"]
        schema = response_schema(index.spec, index.by_key[op]["operation"])
        paths = nodes_at(index.spec, schema, split_pointer(check["collection_pointer"]))
        array = any(n.get("type") == "array" or "items" in n for n in paths)
        base = check["collection_pointer"] + ("/*" if array else "")
        pointers = [check["field_pointer"], check["order_by"]] + list(
            json.loads(check["selector_json"])
        )
        for pointer in pointers:
            if pointer:
                found.add((op, "response", base + pointer))
    for guard in plan.get("preservations", []):
        source = requests.get(guard["source_request_id"])
        if source:
            for pointer in [guard["source_pointer"]] + list(
                json.loads(guard["source_identity_json"])
            ):
                found.add((source["operation"], "response", pointer))
    return found


class Evidence:
    def __init__(self, indexes, tasks, arm):
        self.indexes, self.tasks, self.arm = indexes, {t["id"]: t for t in tasks}, arm
        self.available = {p: set() for p in indexes}
        self.hints, self.cards, self.reads, self.selected = {}, {}, {}, {}
        self.fallback_used = False
        for task in tasks:
            p = task["provider"]
            hits = indexes[p].search(task["task"], 20)
            self.hints[task["id"]] = hits[:8]
            self.available[p].update(
                indexes[p].by_key if arm == "full" else [h["operation"] for h in hits]
            )
            for hit in hits[:3]:
                self.cards[p + "::" + hit["operation"]] = root_names(indexes[p], hit["operation"])

    def discovery_payload(self, sources):
        return {
            "round": 1,
            "tasks": list(self.tasks.values()),
            "source_info": sources,
            "operation_directories": {
                p: [line for line in directory(i) if line.split("\t", 1)[0] in self.available[p]]
                for p, i in self.indexes.items()
            },
            "search_hints": self.hints,
            "initial_schema_cards": {
                k: root_names(self.indexes[k.split("::", 1)[0]], value["operation"])
                for k, value in self.cards.items()
            },
        }

    def select(self, discovery):
        self.cards = {k: v for k, v in self.cards.items() if "response_schema" in v}
        for selection in discovery["plans"]:
            identity = selection["task_id"]
            p = self.tasks[identity]["provider"]
            self.selected[identity] = []
            for operation in selection["operations"]:
                if operation not in self.available[p]:
                    raise ValueError(
                        "Discovery selected an operation outside the supplied evidence"
                    )
                self.add_operation(identity, operation)
            for requested in selection["schema_reads"]:
                self.add_read(
                    identity, requested["operation"], requested["section"], requested["pointer"]
                )
        if self.fallback_used:
            active = {
                self.tasks[i]["provider"] + "::" + op
                for i, ops in self.selected.items()
                for op in ops
            }
            self.cards = {k: v for k, v in self.cards.items() if k in active}
            self.reads = {
                k: v for k, v in self.reads.items() if k[1] in self.selected.get(k[0], [])
            }

    def add_operation(self, identity, operation):
        p = self.tasks[identity]["provider"]
        if operation not in self.indexes[p].by_key:
            return False
        self.available[p].add(operation)
        operations = self.selected.setdefault(identity, [])
        if operation in operations:
            return False
        if operation not in operations:
            if len(operations) >= 12:
                return False
            operations.append(operation)
        self.cards[p + "::" + operation] = card(self.indexes[p], operation, depth=1)
        return True

    def add_read(self, identity, operation, section, pointer):
        p = self.tasks[identity]["provider"]
        if operation not in self.available[p]:
            return False
        key = (identity, operation, section, pointer)
        if key in self.reads:
            return False
        self.reads[key] = {
            "task_id": identity,
            "requested": {"operation": operation, "section": section, "pointer": pointer},
            "result": read(self.indexes[p], operation, section, pointer),
        }
        return True

    def expand(self, batch, iteration):
        events, feedback = [], {}
        unresolved = False
        for plan in batch["plans"]:
            identity = plan["task_id"]
            task = self.tasks[identity]
            p, index = task["provider"], self.indexes[task["provider"]]
            issues = preservation_issues(plan)
            feedback[identity] = issues
            searchable = [
                n for n in plan["retrieval_needs"] if n["kind"] in ["operation", "schema"]
            ]
            partial = [r for r in plan["requirements"] if r["coverage"] == "partial"]
            if searchable or issues or partial:
                unresolved = True
            queries = [n["query"] for n in searchable if n["kind"] == "operation" and n["query"]]
            queries += plan["followup_queries"] if partial else []
            if issues:
                # Query derived from explicit missing-preservation policy, not an endpoint mapping.
                queries.append(
                    "Retrieve current subscription item quantity and subscription identity"
                )
            for query in queries[:4]:
                hits = index.search(query, 8)
                for hit in hits:
                    if hit["operation"].startswith("GET ") and self.add_operation(
                        identity, hit["operation"]
                    ):
                        events.append(
                            {"task_id": identity, "kind": "gap_query", "query": query, **hit}
                        )
            if searchable or issues or partial:
                for hit in neighbors(index, [r["operation"] for r in plan["requests"]]):
                    if self.add_operation(identity, hit["operation"]):
                        events.append({"task_id": identity, "kind": "resource_graph", **hit})
            requested = [
                (n["operation"], n["section"], n["pointer"])
                for n in searchable
                if n["kind"] == "schema"
            ]
            requested += sorted(used_reads(index, plan))
            count = 0
            for operation, section, pointer in requested:
                if count >= 16:
                    break
                if operation not in self.available[p] and operation in index.by_key:
                    self.add_operation(identity, operation)
                if self.add_read(identity, operation, section, pointer):
                    count += 1
                    events.append(
                        {
                            "task_id": identity,
                            "kind": "schema_read",
                            "operation": operation,
                            "section": section,
                            "pointer": pointer,
                        }
                    )
        if iteration == 2 and unresolved and not self.fallback_used:
            self.fallback_used = True
            for p, index in self.indexes.items():
                self.available[p].update(index.by_key)
            events.append(
                {
                    "kind": "complete_directory_fallback",
                    "reason": "Public evidence gaps remain after bounded targeted expansion",
                }
            )
        return {
            "iteration": iteration,
            "events": events,
            "public_validation_feedback": feedback,
            "fallback_used": self.fallback_used,
        }

    def plan_payload(self, sources, guides, discovery, previous=None, expansion=None):
        payload = {
            "round": 2,
            "tasks": list(self.tasks.values()),
            "source_info": sources,
            "selected_schema_cards": self.cards,
            "targeted_schema_reads": list(self.reads.values()),
            "official_pagination_guides": guides,
            "discovery": discovery,
        }
        if previous is not None:
            payload.update(previous_plan=previous, expansion=expansion)
            if not self.fallback_used:
                payload["operation_directories"] = {
                    p: [
                        line for line in directory(i) if line.split("\t", 1)[0] in self.available[p]
                    ]
                    for p, i in self.indexes.items()
                }
        if len(json.dumps(payload)) > 750000:

            def strip_descriptions(value):
                if isinstance(value, dict):
                    return {
                        k: strip_descriptions(v) for k, v in value.items() if k != "description"
                    }
                if isinstance(value, list):
                    return [strip_descriptions(v) for v in value]
                return value

            payload = strip_descriptions(payload)
            payload["projection_adjustments"] = [
                "Descriptions removed to meet context budget; property names, types, enums, pointers and exact targeted reads retained"
            ]
        if len(json.dumps(payload)) > 850000:
            from docs_lab.source import request_schema, response_schema
            from docs_lab.v2.retrieval import tree

            for key, value in list(payload["selected_schema_cards"].items()):
                provider, operation = key.split("::", 1)
                index = self.indexes[provider]
                op = index.by_key[operation]["operation"]
                value["request_schema"] = tree(
                    index.spec, request_schema(index.spec, op), max_depth=0
                )
                value["response_schema"] = tree(
                    index.spec, response_schema(index.spec, op), max_depth=0
                )
                value["projection"] = (
                    "Root field names retained at depth zero; targeted branches provide deeper evidence"
                )
            payload.setdefault("projection_adjustments", []).append(
                "Root cards projected to depth zero; no root child names removed; targeted reads retained"
            )
        return payload
