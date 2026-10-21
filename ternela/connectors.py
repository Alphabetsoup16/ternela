# SPDX-License-Identifier: Apache-2.0
"""Stable connector entry points backed by an explicit, immutable provider registry."""

from dataclasses import dataclass
from inspect import signature
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from ternela.specification import Index
from ternela.storage import canonical, sha

from .errors import RecipeArgumentsError
from .providers import BUILTINS
from .providers.base import PublicConnector
from .providers.npm import valid_integrity  # noqa: F401 — stable helper import
from .providers.release import ReleaseConnector, exact_version
from .transport import read_json

SOURCE_FILES = (
    "ternela/connectors.py",
    "ternela/transport.py",
    "ternela/schema.py",
    "ternela/specification.py",
    *(
        "ternela/providers/" + path.name
        for path in sorted(Path(__file__).with_name("providers").glob("*.py"))
    ),
)


@dataclass(frozen=True, init=False)
class ConnectorRegistry:
    connectors: Mapping[str, PublicConnector]

    def __init__(self, connectors):
        by_name = {}
        for connector in connectors:
            if not isinstance(connector, PublicConnector):
                raise ValueError("Registry entries must implement PublicConnector")
            if connector.provider in by_name:
                raise ValueError("Duplicate connector identifier")
            by_name[connector.provider] = connector
        object.__setattr__(self, "connectors", MappingProxyType(by_name))

    def get(self, provider):
        if not isinstance(provider, str) or provider not in self.connectors:
            raise ValueError("No anonymous live connector for this provider")
        return self.connectors[provider]

    def supports(self, provider, requests):
        if not isinstance(provider, str) or provider not in self.connectors:
            return False
        connector = self.get(provider)
        return bool(requests) and all(
            isinstance(request, dict) and connector.accepts(request.get("operation"))
            for request in requests
        )


CONNECTORS = ConnectorRegistry(BUILTINS)
REGISTRIES = MappingProxyType(
    {
        item.provider: (item.host, item.operation, item.documentation)
        for item in BUILTINS
        if isinstance(item, ReleaseConnector)
    }
)
PUBLIC = frozenset(CONNECTORS.connectors)


def connector_descriptors():
    """JSON-safe UI/docs metadata from the same registry that accepts recipes."""
    return [connector.descriptor() for connector in CONNECTORS.connectors.values()]


def release_providers():
    return tuple(
        name
        for name, connector in CONNECTORS.connectors.items()
        if isinstance(connector, ReleaseConnector)
    )


def recipe_providers():
    return tuple(
        name
        for name, connector in CONNECTORS.connectors.items()
        if callable(getattr(connector, "recipe", None))
    )


def recipe_plan(provider, context):
    """Only explicit builders on registered connectors are callable through this route."""
    connector = CONNECTORS.get(provider)
    builder = getattr(connector, "recipe", None)
    if not callable(builder):
        raise ValueError("This connector has no reviewed recipe")
    if not isinstance(context, dict):
        raise ValueError("Recipe context must be an object")
    try:
        signature(builder).bind(**context)
    except TypeError:
        raise RecipeArgumentsError("Missing or unexpected recipe arguments") from None
    task, plan = builder(**context)
    phases = {check["phase"] for check in plan["checks"]} | {
        requirement["phase"] for requirement in plan["requirements"]
    }
    if len(phases) != 1 or not phases <= {"before", "after"}:
        raise ValueError("A reviewed recipe must declare exactly one business phase")
    return task, plan, phases.pop()


def supports(provider, requests):
    return CONNECTORS.supports(provider, requests)


def live_mode(provider):
    CONNECTORS.get(provider)
    return f"live public {provider} GET" if provider != "github" else "live public GitHub GET"


def request(provider, call, timeout=15, *, reader=None):
    connector = CONNECTORS.get(provider)
    return connector.acquire(
        call,
        timeout,
        read_json if reader is None else reader,
        contract_provenance(provider) if connector.contract() is not None else None,
    )


def release_connector(provider):
    connector = CONNECTORS.get(provider)
    if not isinstance(connector, ReleaseConnector):
        raise ValueError("This provider has no exact-release recipe")
    return connector


def package_name(provider, value):
    return release_connector(provider).package_name(value)


def version_name(value, provider=None):
    return release_connector(provider).version_name(value) if provider else exact_version(value)


def release_view(provider, body):
    return release_connector(provider).normalize(body)


def contract_spec(provider):
    return CONNECTORS.get(provider).contract()


def contract_provenance(provider):
    connector = CONNECTORS.get(provider)
    if connector.contract() is None:
        raise ValueError(
            "This connector uses its pinned upstream schema, not a local evidence view"
        )
    root = Path(__file__).parent.parent
    code = {name: sha((root / name).read_bytes()) for name in SOURCE_FILES}
    return {
        "provider": provider,
        "kind": "reviewed_local_evidence_view",
        "documentation": connector.documentation,
        "sha256": sha(canonical(connector.contract())),
        "adapter_sha256": sha(canonical(code)),
        "adapter_code": code,
        "limitation": connector.limitation(),
    }


def source_index(provider):
    if (
        isinstance(provider, str)
        and provider in CONNECTORS.connectors
        and CONNECTORS.get(provider).contract() is not None
    ):
        index = Index(provider, CONNECTORS.get(provider).contract())
        index.provenance = contract_provenance(provider)
        return index
    from ternela.sources import SourceIndex

    return SourceIndex(provider)


def release_plan(provider, package, version):
    return release_connector(provider).recipe(package, version)
