# SPDX-License-Identifier: Apache-2.0
"""Strict JSON schema subset used by admitted plans and connector views."""


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_shape(value, schema, path="$"):
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
