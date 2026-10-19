# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from observation_lab.schema import obj
from docs_lab.v2.schema import PLAN as V2_PLAN, DISCOVERY

PRESERVATION = obj({
    "id": {"type": "string"}, "phase": {"type": "string", "enum": ["before", "after"]},
    "source_request_id": {"type": "string"}, "source_pointer": {"type": "string"},
    "source_identity_json": {"type": "string"}, "target_request_id": {"type": "string"},
    "target_pointer": {"type": "string"}, "value_type": {"type": "string", "enum": ["integer", "number", "string", "boolean"]},
    "reason": {"type": "string"},
})
NEED = obj({"kind": {"type": "string", "enum": ["operation", "schema", "policy", "external_fact"]},
            "query": {"type": "string"}, "operation": {"type": "string"}, "section": {"type": "string", "enum": ["request", "response"]},
            "pointer": {"type": "string"}, "reason": {"type": "string"}})
PLAN = deepcopy(V2_PLAN)
PLAN["properties"].update(preservations={"type": "array", "items": PRESERVATION, "maxItems": 12}, retrieval_needs={"type": "array", "items": NEED, "maxItems": 8})
PLAN["required"].extend(["preservations", "retrieval_needs"])
BATCH = obj({"plans": {"type": "array", "items": PLAN}})
