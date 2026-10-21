# SPDX-License-Identifier: Apache-2.0
"""Document-grounded plans, with explicit request binding and phase coverage."""

from .schema import obj

PAGINATION = obj(
    {
        "mode": {"type": "string", "enum": ["none", "link", "cursor"]},
        "items_pointer": {"type": "string"},
        "cursor_parameter": {"type": "string"},
        "next_cursor_pointer": {"type": "string"},
        "has_more_pointer": {"type": "string"},
        "cursor_item_pointer": {"type": "string"},
        "max_pages": {"type": "integer", "minimum": 1, "maximum": 20},
    }
)

REQUEST = obj(
    {
        "id": {"type": "string"},
        "operation": {"type": "string"},
        "bindings_json": {"type": "string"},
        "purpose": {"type": "string"},
        "auth_notes": {"type": "string"},
        "poll_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
        "pagination": PAGINATION,
    }
)
CHECK = obj(
    {
        "id": {"type": "string"},
        "request_id": {"type": "string"},
        "phase": {"type": "string", "enum": ["before", "after"]},
        "collection_pointer": {"type": "string"},
        "selector_json": {"type": "string"},
        "select_mode": {
            "type": "string",
            "enum": ["single", "any", "all", "latest", "count", "single_or_unknown"],
        },
        "order_by": {"type": "string"},
        "field_pointer": {"type": "string"},
        "op": {"type": "string", "enum": ["exists", "eq", "ne", "lt", "lte", "gt", "gte"]},
        "value_json": {"type": "string"},
        "reason": {"type": "string"},
    }
)
PRESERVATION = obj(
    {
        "id": {"type": "string"},
        "phase": {"type": "string", "enum": ["before", "after"]},
        "source_request_id": {"type": "string"},
        "source_pointer": {"type": "string"},
        "source_identity_json": {"type": "string"},
        "target_request_id": {"type": "string"},
        "target_pointer": {"type": "string"},
        "value_type": {"type": "string", "enum": ["integer", "number", "string", "boolean"]},
        "reason": {"type": "string"},
    }
)
NEED = obj(
    {
        "kind": {"type": "string", "enum": ["operation", "schema", "policy", "external_fact"]},
        "query": {"type": "string"},
        "operation": {"type": "string"},
        "section": {"type": "string", "enum": ["request", "response"]},
        "pointer": {"type": "string"},
        "reason": {"type": "string"},
    }
)

PLAN = obj(
    {
        "task_id": {"type": "string"},
        "requests": {"type": "array", "items": REQUEST, "maxItems": 12},
        "checks": {"type": "array", "items": CHECK, "maxItems": 30},
        "requirements": {
            "type": "array",
            "items": obj(
                {
                    "id": {"type": "string"},
                    "phase": {"type": "string", "enum": ["before", "after"]},
                    "text": {"type": "string"},
                    "coverage": {
                        "type": "string",
                        "enum": ["observable", "partial", "unobservable", "missing_input"],
                    },
                    "check_ids": {"type": "array", "items": {"type": "string"}},
                    "limitation": {"type": "string"},
                }
            ),
        },
        "followup_queries": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "schema_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "preservations": {"type": "array", "items": PRESERVATION, "maxItems": 12},
        "retrieval_needs": {"type": "array", "items": NEED, "maxItems": 8},
    }
)
BATCH = obj({"plans": {"type": "array", "items": PLAN}})
