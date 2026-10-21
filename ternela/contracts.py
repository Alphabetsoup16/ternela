# SPDX-License-Identifier: Apache-2.0
"""Immutable internal handoffs and typed persisted references; wire JSON stays compatible."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from types import MappingProxyType
from typing import Any, Literal, NotRequired, TypedDict

from .errors import ObservationConflict, UnsupportedVersion
from .storage import Store, canonical, sha, strict_json

Phase = Literal["before", "after"]
DecisionStatus = Literal["ready", "complete", "blocked", "pending", "unknown", "unverifiable"]
RecordKind = Literal["plan", "receipt"]
RECORD_VERSION = 1


class RecordRef(TypedDict):
    sha256: str
    bytes: int
    gzip_bytes: int
    media_type: str
    record_type: NotRequired[RecordKind]
    record_version: NotRequired[int]


class Decision(TypedDict):
    status: DecisionStatus
    checks: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    stale: NotRequired[bool]
    preservation_failures: NotRequired[list[dict[str, Any]]]


class Receipt(TypedDict):
    watch_id: str
    event_id: str
    revision: int
    observed_at: float
    evaluated_at: float
    expires_at: float
    phase: Phase
    result: Decision
    observations: dict[str, Any]
    input: dict[str, Any]
    plan_sha256: str
    source_evidence: dict[str, Any]
    mode: str


def validate_record_ref(ref: RecordRef, kind: RecordKind):
    """Check explicit versions before any payload is interpreted."""
    if "record_type" in ref or "record_version" in ref:
        if (
            ref.get("record_type") != kind
            or type(ref.get("record_version")) is not int
            or ref["record_version"] != RECORD_VERSION
        ):
            raise UnsupportedVersion(f"Unsupported {kind} record version")


def read_record(store: Store, ref: RecordRef, kind: RecordKind, *, max_bytes=None):
    """Legacy unversioned references are v0; their payload hashes stay unchanged."""
    validate_record_ref(ref, kind)
    return store.get(ref, max_bytes=max_bytes)


def write_record(store: Store, value, kind: RecordKind) -> RecordRef:
    return {**store.put(value), "record_type": kind, "record_version": RECORD_VERSION}


def freeze_json(value):
    """Own containers once while sharing immutable scalar values, including large strings."""

    def freeze(item, depth=0):
        # Internal receipt/envelope wrappers add levels to externally bounded JSON.
        if depth > 128:
            raise ValueError("Internal JSON nesting exceeds 128 levels")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("JSON object keys must be strings")
            return MappingProxyType({key: freeze(child, depth + 1) for key, child in item.items()})
        if type(item) is list:
            return tuple(freeze(child, depth + 1) for child in item)
        if item is None or type(item) in (str, int, bool):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        raise ValueError("Expected finite JSON data")

    return freeze(value)


def thaw_json(value):
    if isinstance(value, Mapping):
        return {key: thaw_json(child) for key, child in value.items()}
    if type(value) is tuple:
        return [thaw_json(child) for child in value]
    return value


@dataclass(frozen=True, init=False)
class FrozenJSON:
    """Immutable validated tree; encode only when a content hash or bytes are needed."""

    _tree: Any
    _encoded: bytes | None

    def __init__(self, data: bytes):
        if type(data) is not bytes:
            raise TypeError("FrozenJSON requires immutable bytes")
        object.__setattr__(self, "_tree", freeze_json(strict_json(data)))
        object.__setattr__(self, "_encoded", data)

    @classmethod
    def capture(cls, value):
        result = object.__new__(cls)
        object.__setattr__(result, "_tree", freeze_json(value))
        object.__setattr__(result, "_encoded", None)
        return result

    def value(self):
        return thaw_json(self._tree)

    @cached_property
    def data(self):
        return self._encoded if self._encoded is not None else canonical(self.value())

    @property
    def sha256(self):
        return sha(self.data)


@dataclass(frozen=True)
class WatchSnapshot:
    identity: str
    revision: int
    phase: Phase
    ttl: float
    task_ref: Mapping
    plan_ref: Mapping
    task: FrozenJSON
    plan: FrozenJSON

    def __post_init__(self):
        if (
            self.task.sha256 != self.task_ref["sha256"]
            or self.plan.sha256 != self.plan_ref["sha256"]
        ):
            raise ObservationConflict("Snapshot payload does not match its content reference")
        object.__setattr__(self, "task_ref", MappingProxyType(dict(self.task_ref)))
        object.__setattr__(self, "plan_ref", MappingProxyType(dict(self.plan_ref)))

    @property
    def binding(self):
        return binding_hash(self.plan.value(), self.task.value()["context"], self.phase)


def binding_hash(plan, context, phase: Phase):
    return sha(canonical([plan, context, phase]))


@dataclass(frozen=True)
class Acquisition:
    binding: str
    observed_at: float
    snapshots: FrozenJSON
    result: FrozenJSON
    observations: FrozenJSON


@dataclass(frozen=True)
class PreparedObservation:
    watch_id: str
    plan_sha256: str
    task_sha256: str
    phase: Phase
    event_id: str
    revision: int
    observed_at: float
    fingerprint: str
    receipt: FrozenJSON

    def __post_init__(self):
        value = self.receipt.value()
        expected = {
            "watch_id": self.watch_id,
            "plan_sha256": self.plan_sha256,
            "phase": self.phase,
            "event_id": self.event_id,
            "revision": self.revision,
            "observed_at": self.observed_at,
        }
        if any(value.get(key) != expected_value for key, expected_value in expected.items()):
            raise ObservationConflict("Prepared metadata does not match its frozen receipt")


@dataclass(frozen=True)
class LeaseFence:
    watch_id: str
    token: str


@dataclass(frozen=True)
class ScheduleClaim:
    watch_id: str
    lease_token: str
    revision: int
    interval: float
    failures: int
    provider_key: str

    @classmethod
    def from_mapping(cls, value):
        return cls(
            *(
                value[key]
                for key in (
                    "watch_id",
                    "lease_token",
                    "revision",
                    "interval",
                    "failures",
                    "provider_key",
                )
            )
        )
