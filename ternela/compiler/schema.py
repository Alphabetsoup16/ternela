# SPDX-License-Identifier: Apache-2.0
"""Compiler discovery schema; executable plans share the runtime's exact contract."""

from ternela.plan_schema import BATCH as BATCH
from ternela.plan_schema import PLAN as PLAN
from ternela.schema import obj

DISCOVERY = obj(
    {
        "plans": {
            "type": "array",
            "items": obj(
                {
                    "task_id": {"type": "string"},
                    "operations": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "schema_reads": {
                        "type": "array",
                        "maxItems": 6,
                        "items": obj(
                            {
                                "operation": {"type": "string"},
                                "section": {"type": "string", "enum": ["request", "response"]},
                                "pointer": {"type": "string"},
                            }
                        ),
                    },
                    "rationale": {"type": "string"},
                }
            ),
        }
    }
)
