# SPDX-License-Identifier: Apache-2.0
"""Document-grounded plans, with explicit request binding and phase coverage."""

from observation_lab.schema import obj

REQUEST = obj({
    "id": {"type": "string"},
    "operation": {"type": "string"},
    "bindings_json": {"type": "string"},
    "purpose": {"type": "string"},
    "auth_notes": {"type": "string"},
    "poll_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
})
CHECK = obj({
    "id": {"type": "string"}, "request_id": {"type": "string"},
    "phase": {"type": "string", "enum": ["before", "after"]},
    "collection_pointer": {"type": "string"},
    "selector_json": {"type": "string"},
    "select_mode": {"type": "string", "enum": ["single", "any", "all", "latest"]},
    "order_by": {"type": "string"},
    "field_pointer": {"type": "string"},
    "op": {"type": "string", "enum": ["exists", "eq", "ne", "lt", "lte", "gt", "gte"]},
    "value_json": {"type": "string"},
    "reason": {"type": "string"},
})
PLAN = obj({
    "task_id": {"type": "string"},
    "requests": {"type": "array", "items": REQUEST, "maxItems": 12},
    "checks": {"type": "array", "items": CHECK, "maxItems": 30},
    "requirements": {"type": "array", "items": obj({
        "id": {"type": "string"}, "phase": {"type": "string", "enum": ["before", "after"]},
        "text": {"type": "string"},
        "coverage": {"type": "string", "enum": ["observable", "partial", "unobservable", "missing_input"]},
        "check_ids": {"type": "array", "items": {"type": "string"}},
        "limitation": {"type": "string"},
    })},
    "followup_queries": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "schema_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    "limitations": {"type": "array", "items": {"type": "string"}},
})
BATCH = obj({"plans": {"type": "array", "items": PLAN}})
