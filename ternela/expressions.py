# SPDX-License-Identifier: Apache-2.0
"""Typed JSON comparisons and context bindings; no arbitrary expressions."""

import math
import operator

from .specification import pointer

OPS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
}


def bind(value, context):
    if isinstance(value, str) and value.startswith("$context."):
        return pointer(context, "/" + value[len("$context.") :].replace(".", "/"))
    if isinstance(value, dict):
        return {k: bind(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [bind(v, context) for v in value]
    return value


def compare(left, op, right):
    if any(type(value) is float and not math.isfinite(value) for value in (left, right)):
        raise ValueError("Comparison requires finite evidence")
    if op in ("eq", "ne"):
        # Prevent True == 1 from accidentally satisfying a typed condition.
        same_type = type(left) is type(right) or (
            type(left) in (int, float) and type(right) in (int, float)
        )
        equal = same_type and left == right
        return equal if op == "eq" else not equal
    if type(left) not in (int, float) or type(right) not in (int, float):
        raise ValueError("Ordering requires numeric evidence")
    return OPS[op](left, right)
