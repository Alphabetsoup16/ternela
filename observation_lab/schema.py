# SPDX-License-Identifier: Apache-2.0
"""The planner emits data, never executable Python or arbitrary expressions."""


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


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
    """Small validator for precisely the JSON Schema subset above; no coercion."""
    if "anyOf" in schema:
        for alternative in schema["anyOf"]:
            try:
                validate_shape(value, alternative, path)
                return
            except ValueError:
                pass
        raise ValueError(f"{path}: expected a scalar string, number or boolean")
    kind = schema["type"]
    valid = {
        "object": lambda: type(value) is dict,
        "array": lambda: type(value) is list,
        "string": lambda: type(value) is str,
        "number": lambda: type(value) in (int, float),
        "integer": lambda: type(value) is int,
        "boolean": lambda: type(value) is bool,
    }[kind]()
    if not valid:
        raise ValueError(f"{path}: expected {kind}")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError(f"{path}: missing or extra keys")
        for key, sub_schema in schema["properties"].items():
            validate_shape(value[key], sub_schema, f"{path}.{key}")
    if kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: too few items")
        if len(value) > schema.get("maxItems", 10000):
            raise ValueError(f"{path}: too many items")
        for i, item in enumerate(value):
            validate_shape(item, schema["items"], f"{path}[{i}]")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid enum")
    if kind in ("integer", "number"):
        import math

        if type(value) is float and not math.isfinite(value):
            raise ValueError(f"{path}: number must be finite")
        if value < schema.get("minimum", float("-inf")) or value > schema.get(
            "maximum", float("inf")
        ):
            raise ValueError(f"{path}: number out of bounds")
