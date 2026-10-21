# SPDX-License-Identifier: Apache-2.0
"""A narrow anonymous-read boundary; scheduling and decisions belong to the runtime."""

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import quote, urlencode, urlsplit

from ternela.schema import validate_shape
from ternela.specification import response_schema


@dataclass(frozen=True)
class PublicConnector:
    provider: str
    host: str
    operation: str
    documentation: str
    operation_prefix: bool = False
    allow_query: bool = False
    allow_continuation: bool = False
    display_name: ClassVar[str] = ""

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", self.provider):
            raise ValueError("Invalid connector identifier")
        if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", self.host):
            raise ValueError("Connector host must be a fixed DNS name without credentials or port")
        if not self.operation.startswith("GET /") or any(c in self.operation for c in "?#\r\n"):
            raise ValueError("Public connectors must declare a GET path")
        if self.operation_prefix and not self.operation.endswith("/"):
            raise ValueError("Operation prefixes must end at a path boundary")
        if urlsplit(self.documentation).scheme != "https":
            raise ValueError("Connector documentation must use HTTPS")

    def accepts(self, operation):
        return isinstance(operation, str) and (
            operation.startswith(self.operation)
            if self.operation_prefix
            else operation == self.operation
        )

    def path_parameter(self, name, value):
        if type(value) not in (str, int):
            raise ValueError("Path parameters must be strings or integers")
        return str(value)

    def contract(self):
        """None means the provider JSON is evaluated directly against pinned source schemas."""
        return None

    def limitation(self):
        return "Public API evidence only; no independent real-world verification"

    def descriptor(self):
        return {
            "provider": self.provider,
            "label": self.display_name or self.provider,
            "documentation": self.documentation,
            "limitation": self.limitation(),
            "recipe": None,
        }

    def normalize(self, body):
        return deepcopy(body)

    def acquire(self, call, timeout, reader, provenance=None):
        if not self.accepts(call.get("operation")):
            raise ValueError("This operation has no reviewed public connector")
        bindings = call["bindings"]
        if not isinstance(bindings, dict) or set(bindings) - {"path", "query", "body", "header"}:
            raise ValueError("Anonymous connectors accept only path and query bindings")
        if bindings.get("body", {}) != {} or bindings.get("header", {}) != {}:
            raise ValueError(
                "Anonymous connectors do not accept request bodies or header overrides"
            )
        path = call["operation"][4:]
        path_bindings = bindings.get("path", {})
        if not isinstance(path_bindings, dict) or not isinstance(bindings.get("query", {}), dict):
            raise ValueError("Path and query bindings must be objects")
        for name, value in path_bindings.items():
            if "{" + name + "}" not in path:
                raise ValueError("Unknown path binding")
            path = path.replace("{" + name + "}", quote(self.path_parameter(name, value), safe=""))
        if "{" in path or any(segment in (".", "..") for segment in path.split("/")):
            raise ValueError("Unresolved or invalid path")
        query = bindings.get("query", {})
        if query and not self.allow_query:
            raise ValueError("Connector does not permit query parameters")
        next_url = call.get("next_url")
        if next_url is not None and not self.allow_continuation:
            raise ValueError("Connector does not permit continuation URLs")
        url = next_url or "https://" + self.host + path + (
            "?" + urlencode(query, doseq=True) if query else ""
        )
        address = urlsplit(url)
        if (
            address.scheme != "https"
            or address.netloc != self.host
            or address.path != path
            or address.username
            or address.password
            or address.fragment
        ):
            raise ValueError("Continuation must preserve the approved origin and resource")
        envelope = deepcopy(reader(url, self.host, timeout))
        contract = self.contract()
        if contract is not None and 200 <= envelope["http_status"] < 300:
            envelope["source_body"] = deepcopy(envelope["body"])
            envelope["view_contract"] = provenance
            try:
                normalized = self.normalize(envelope["body"])
                schema = response_schema(contract, contract["paths"][self.operation[4:]]["get"])
                validate_shape(normalized, schema)
                envelope["body"] = normalized
            except (KeyError, TypeError, ValueError, IndexError):
                envelope.update(
                    source_http_status=envelope["http_status"],
                    http_status=0,
                    body=None,
                    error="Provider metadata does not satisfy the reviewed view contract",
                )
        return envelope
