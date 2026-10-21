# SPDX-License-Identifier: Apache-2.0
"""Reusable exact-release recipe with explicit provider evidence fields."""

import json
import re
from dataclasses import dataclass
from typing import ClassVar

from ternela.storage import canonical, sha

from .base import PublicConnector


@dataclass(frozen=True)
class EvidenceField:
    name: str
    kind: str
    op: str
    value: object
    reason: str


def exact_version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}", value):
        raise ValueError("Use an exact package version without slashes or whitespace")
    return value


@dataclass(frozen=True)
class ReleaseConnector(PublicConnector):
    purpose: ClassVar[str] = ""
    package_example: ClassVar[str] = "package-name"
    version_example: ClassVar[str] = "1.0.0"
    conditions: ClassVar[tuple[EvidenceField, ...]] = ()

    def limitation(self):
        return "Metadata only; no package download, artifact verification or arbitrary task compilation"

    def descriptor(self):
        result = super().descriptor()
        result.update(
            label=self.display_name,
            recipe={
                "label": self.display_name + " package version",
                "hint": self.limitation(),
                "fields": [
                    {
                        "key": "package",
                        "label": "Package",
                        "placeholder": self.package_example,
                        "max_length": 214,
                    },
                    {
                        "key": "version",
                        "label": "Exact version",
                        "placeholder": self.version_example,
                        "max_length": 128,
                    },
                ],
            },
        )
        return result

    def package_name(self, value):
        raise NotImplementedError

    def version_name(self, value):
        return exact_version(value)

    def path_parameter(self, name, value):
        if name == "package":
            return self.package_name(value)
        if name == "version":
            return self.version_name(value)
        raise ValueError("Unknown release path parameter")

    def fields(self):
        return (
            EvidenceField(
                "name", "string", "eq", "$context.package", "Registry name must match {package}."
            ),
            EvidenceField(
                "version",
                "string",
                "eq",
                "$context.version",
                "Registry version must match {version}.",
            ),
            *self.conditions,
        )

    def contract(self):
        path = self.operation[4:]
        properties = {field.name: {"type": field.kind} for field in self.fields()}
        return {
            "openapi": "3.0.0",
            "paths": {
                path: {
                    "get": {
                        "summary": "Reviewed local release evidence view; see upstream documentation and adapter mapping",
                        "parameters": [
                            {
                                "name": key,
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                            for key in ("package", "version")
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": properties,
                                            "required": list(properties),
                                            "additionalProperties": False,
                                        }
                                    }
                                }
                            }
                        },
                    }
                }
            },
        }

    def recipe(self, package, version):
        provider = self.provider
        package, version = self.package_name(package), self.version_name(version)
        identity = "public-" + provider + "-" + sha(canonical([package, version]))[:16]
        purpose = self.purpose
        task = {
            "id": identity,
            "provider": provider,
            "task": f"Wait for exact {provider} package {package} version {version} with {purpose}. Metadata only.",
            "context": {"package": package, "version": version},
        }
        fields = self.fields()
        checks = [
            {
                "id": field.name,
                "request_id": "release",
                "phase": "after",
                "collection_pointer": "",
                "field_pointer": "/" + field.name,
                "selector_json": "{}",
                "select_mode": "single",
                "order_by": "",
                "op": field.op,
                "value_json": json.dumps(field.value),
                "reason": field.reason.format(package=package, version=version),
            }
            for field in fields
        ]
        pagination = {
            "mode": "none",
            "items_pointer": "",
            "has_more_pointer": "",
            "next_cursor_pointer": "",
            "cursor_item_pointer": "",
            "cursor_parameter": "",
            "max_pages": 1,
        }
        plan = {
            "task_id": identity,
            "requests": [
                {
                    "id": "release",
                    "operation": self.operation,
                    "bindings_json": json.dumps(
                        {"path": {"package": "$context.package", "version": "$context.version"}}
                    ),
                    "purpose": purpose,
                    "auth_notes": "Anonymous public metadata; no account or credential",
                    "poll_interval_seconds": 300,
                    "pagination": pagination,
                }
            ],
            "checks": checks,
            "requirements": [
                {
                    "id": "release_metadata",
                    "phase": "after",
                    "text": task["task"],
                    "coverage": "observable",
                    "check_ids": [check["id"] for check in checks],
                    "limitation": "Registry metadata does not prove artifact safety, integrity of downloaded bytes, or installation success.",
                }
            ],
            "followup_queries": [],
            "schema_refs": [self.documentation],
            "retrieval_needs": [],
            "limitations": ["Reviewed deterministic recipe; not a model-compiled task."],
            "preservations": [],
        }
        return task, plan
