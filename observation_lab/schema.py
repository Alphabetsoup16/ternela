# SPDX-License-Identifier: Apache-2.0
"""The planner emits data, never executable Python or arbitrary expressions."""

from ternela.schema import obj
from ternela.schema import validate_shape as _validate_shape

CHECK = obj(
    {
        "id": {"type": "string"},
        "phase": {"type": "string", "enum": ["before", "after"]},
        "tool": {"type": "string"},
        "field": {"type": "string"},
        "op": {"type": "string", "enum": ["eq", "ne", "lt", "lte", "gt", "gte", "contains"]},
        "value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]},
        "max_age_seconds": {"type": "integer", "minimum": 1, "maximum": 60},
        "reason": {"type": "string"},
    }
)
PLAN = obj(
    {
        "task_id": {"type": "string"},
        "checks": {"type": "array", "items": CHECK, "maxItems": 50},
        "requirements": {
            "type": "array",
            "items": obj(
                {
                    "text": {"type": "string"},
                    "check_ids": {"type": "array", "items": {"type": "string"}},
                }
            ),
        },
        "unresolved": {"type": "array", "items": {"type": "string"}},
        "poll_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 30},
        "receipt_timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
    }
)
BATCH = obj({"plans": {"type": "array", "items": PLAN}})


def validate_shape(value, schema=BATCH, path="$"):
    return _validate_shape(value, schema, path)
