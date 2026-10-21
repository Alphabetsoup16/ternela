# SPDX-License-Identifier: Apache-2.0
"""Exact GitHub Status leaf-component readiness from one public summary."""

import json
import re
from dataclasses import dataclass
from typing import ClassVar

from ternela.schema import obj

from .base import PublicConnector

PAGE_ID = "kctbh9vrtdwd"
COMPONENT_STATES = (
    "operational",
    "degraded_performance",
    "partial_outage",
    "major_outage",
    "under_maintenance",
)


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]{12}", value):
        raise ValueError("Use an exact 12-character public status identifier")
    return value


def same_page(value):
    if value != PAGE_ID:
        raise ValueError("Status metadata belongs to a different page")


@dataclass(frozen=True)
class GithubStatusConnector(PublicConnector):
    active_incidents: ClassVar[frozenset[str]] = frozenset(
        ("investigating", "identified", "monitoring")
    )
    inactive_incidents: ClassVar[frozenset[str]] = frozenset(("resolved", "postmortem"))
    active_maintenance: ClassVar[frozenset[str]] = frozenset(("in_progress", "verifying"))
    inactive_maintenance: ClassVar[frozenset[str]] = frozenset(("scheduled", "completed"))

    def descriptor(self):
        result = super().descriptor()
        result.update(
            label="GitHub Status",
            recipe={
                "label": "GitHub service status",
                "hint": self.limitation(),
                "fields": [
                    {
                        "key": "component_id",
                        "label": "Service",
                        "options": {
                            "br0l2tvcx85d": "GitHub Actions",
                            "brv1bkgrwx7q": "GitHub API requests",
                            "4230lsnqdsld": "GitHub webhooks",
                        },
                    }
                ],
            },
        )
        return result

    def limitation(self):
        return "Vendor-reported current status only; not an independent health probe, future availability guarantee or authorization to deploy."

    def contract(self):
        row = obj(
            {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "status": {"type": "string", "enum": list(COMPONENT_STATES)},
                "incident_count": {"type": "integer", "minimum": 0},
                "maintenance_count": {"type": "integer", "minimum": 0},
            }
        )
        schema = obj(
            {
                "page_id": {"type": "string", "enum": [PAGE_ID]},
                "components": {"type": "array", "items": row},
            }
        )
        return {
            "openapi": "3.0.0",
            "paths": {
                self.operation[4:]: {
                    "get": {
                        "summary": "Reviewed local view of a complete public status summary; exact leaf-component identity, current incidents and active maintenance",
                        "parameters": [],
                        "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
                    }
                }
            },
        }

    def normalize(self, body):
        same_page(body["page"]["id"])
        components, incidents, maintenance = (
            body["components"],
            body["incidents"],
            body["scheduled_maintenances"],
        )
        if any(not isinstance(value, list) for value in (components, incidents, maintenance)):
            raise ValueError("Status components, incidents and maintenance must be explicit arrays")
        rows, seen = {}, set()
        for component in components:
            key = identity(component["id"])
            same_page(component["page_id"])
            if key in seen or type(component["group"]) is not bool:
                raise ValueError("Duplicate component or unknown group membership")
            seen.add(key)
            if component["group"]:
                continue
            status = component["status"]
            if (
                not isinstance(status, str)
                or status not in COMPONENT_STATES
                or not isinstance(component["name"], str)
            ):
                raise ValueError("Unknown component status or malformed component name")
            rows[key] = {
                "id": key,
                "name": component["name"],
                "status": status,
                "incident_count": 0,
                "maintenance_count": 0,
            }
        self._attach(
            incidents, rows, self.active_incidents, self.inactive_incidents, "incident_count"
        )
        self._attach(
            maintenance,
            rows,
            self.active_maintenance,
            self.inactive_maintenance,
            "maintenance_count",
        )
        return {"page_id": PAGE_ID, "components": list(rows.values())}

    @staticmethod
    def _attach(records, rows, active, inactive, field):
        seen = set()
        for record in records:
            key = identity(record["id"])
            same_page(record["page_id"])
            if key in seen:
                raise ValueError("Duplicate status record")
            seen.add(key)
            status = record["status"]
            if not isinstance(status, str) or status not in active | inactive:
                raise ValueError("Unknown incident or maintenance state")
            if status in inactive:
                continue
            associations = record["components"]
            if not isinstance(associations, list) or not associations:
                raise ValueError("Active record has unknown component scope")
            attached = set()
            for association in associations:
                component = identity(association["id"])
                same_page(association["page_id"])
                if component not in rows or component in attached:
                    raise ValueError(
                        "Active record names an unknown/group component or duplicate association"
                    )
                attached.add(component)
                rows[component][field] += 1

    def recipe(self, component_id):
        component_id = identity(component_id)
        task = {
            "id": "github-status-" + component_id,
            "provider": self.provider,
            "task": f"Before proceeding, require GitHub Status leaf component {component_id} to be reported operational, with no unresolved incident and no active maintenance attached to it. Vendor-reported metadata only.",
            "context": {"component_id": component_id},
        }
        checks = [
            {
                "id": "page",
                "request_id": "summary",
                "phase": "before",
                "collection_pointer": "",
                "field_pointer": "/page_id",
                "selector_json": "{}",
                "select_mode": "single",
                "order_by": "",
                "op": "eq",
                "value_json": json.dumps(PAGE_ID),
                "reason": "Require the exact GitHub Status page.",
            }
        ]
        for field, value, reason in (
            ("status", "operational", "The exact component must be reported operational."),
            ("incident_count", 0, "No unresolved incident may be attached to the component."),
            ("maintenance_count", 0, "No active maintenance may be attached to the component."),
        ):
            checks.append(
                {
                    "id": field,
                    "request_id": "summary",
                    "phase": "before",
                    "collection_pointer": "/components",
                    "field_pointer": "/" + field,
                    "selector_json": json.dumps({"/id": "$context.component_id"}),
                    "select_mode": "single_or_unknown",
                    "order_by": "",
                    "op": "eq",
                    "value_json": json.dumps(value),
                    "reason": reason,
                }
            )
        pagination = {
            "mode": "link",
            "items_pointer": "/components",
            "has_more_pointer": "",
            "next_cursor_pointer": "",
            "cursor_item_pointer": "",
            "cursor_parameter": "",
            "max_pages": 1,
        }
        plan = {
            "task_id": task["id"],
            "requests": [
                {
                    "id": "summary",
                    "operation": self.operation,
                    "bindings_json": "{}",
                    "purpose": "Read one complete status summary and retain exact component and active-record associations.",
                    "auth_notes": "Anonymous public metadata; no account or credential",
                    "poll_interval_seconds": 300,
                    "pagination": pagination,
                }
            ],
            "checks": checks,
            "requirements": [
                {
                    "id": "component_readiness",
                    "phase": "before",
                    "text": task["task"],
                    "coverage": "observable",
                    "check_ids": [check["id"] for check in checks],
                    "limitation": self.limitation(),
                }
            ],
            "followup_queries": [],
            "schema_refs": [self.documentation],
            "retrieval_needs": [],
            "limitations": [
                self.limitation(),
                "Strict leaf-only summary; unattributed active records and malformed unrelated rows remain unknown. No inference from the global banner or updated_at.",
            ],
            "preservations": [],
        }
        return task, plan
