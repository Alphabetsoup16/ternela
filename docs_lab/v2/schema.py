# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy

from docs_lab.schema import PLAN as OLD_PLAN
from observation_lab.schema import obj

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
PLAN = deepcopy(OLD_PLAN)
request = PLAN["properties"]["requests"]["items"]
request["properties"]["pagination"] = PAGINATION
request["required"].append("pagination")
PLAN["properties"]["checks"]["items"]["properties"]["select_mode"]["enum"].extend(
    ["count", "single_or_unknown"]
)
BATCH = obj({"plans": {"type": "array", "items": PLAN}})
