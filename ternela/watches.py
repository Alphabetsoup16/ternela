# SPDX-License-Identifier: Apache-2.0
"""Watch application service: admit, prepare outside locks, commit through one repository."""

import math
import threading
import time
import uuid

from .connectors import source_index, supports
from .contracts import (
    Acquisition,
    FrozenJSON,
    LeaseFence,
    PreparedObservation,
    Receipt,
    WatchSnapshot,
)
from .database import Database
from .decisions import explain
from .errors import ObservationConflict
from .evaluation import check_details
from .observations import digest, observe, phase_plan, receipt_summary
from .observations import run_observations as run_observations
from .repository import WatchRepository
from .storage import Store
from .validation import admit


class Watches:
    def __init__(self, path, store=None, clock=time.time):
        self.path, self.store, self.clock = str(path), store or Store(), clock
        self._indexes = {}
        self._index_lock = threading.Lock()
        self.repository = WatchRepository(Database(path, self.store, self.now))

    def now(self):
        value = self.clock()
        if (
            type(value) not in (int, float)
            or (type(value) is float and not math.isfinite(value))
            or value < 0
        ):
            raise ValueError("Clock must return a finite, nonnegative timestamp")
        return value

    def connect(self):
        """Compatibility/administration escape hatch; runtime uses repositories."""
        return self.repository.connect()

    def create(self, task, plan, phase="after", ttl=30, indexes=None):
        if phase not in ["before", "after"]:
            raise ValueError("Invalid phase")
        if (
            type(ttl) not in [int, float]
            or (type(ttl) is float and not math.isfinite(ttl))
            or not 1 <= ttl <= 86400
        ):
            raise ValueError("TTL must be 1–86400 seconds")
        if indexes is None:
            with self._index_lock:
                provider = task["provider"]
                if provider not in self._indexes:
                    self._indexes[provider] = source_index(provider)
                indexes = self._indexes
        admission = admit(plan, task, indexes)
        task = dict(task, _admission=admission)
        phase_plan(plan, phase)
        identity = uuid.uuid4().hex
        self.repository.insert(identity, task, plan, phase, ttl)
        return self.get(identity)

    def prepare(
        self,
        identity,
        event_id,
        revision,
        snapshots,
        observed_at=None,
        mode="caller-supplied snapshot",
        *,
        snapshot: WatchSnapshot | None = None,
    ) -> PreparedObservation:
        now = self.now()
        supplied_time = observed_at
        observed_at = now if observed_at is None else observed_at
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 160:
            raise ValueError("Invalid event ID")
        if type(revision) is not int or not 1 <= revision <= 2**63 - 1:
            raise ValueError("Revision must be an integer from 1 to 2^63-1")
        if (
            type(observed_at) not in (int, float)
            or (type(observed_at) is float and not math.isfinite(observed_at))
            or observed_at < 0
            or observed_at > now + 1
        ):
            raise ValueError("Invalid observation time")
        if not isinstance(mode, str):
            raise ValueError("Observation mode must be a string")
        if snapshot is None:
            snapshot = self.repository.snapshot(identity)
        elif not isinstance(snapshot, WatchSnapshot) or snapshot.identity != identity:
            raise ObservationConflict("Snapshot belongs to another watch")
        task, plan = snapshot.task.value(), snapshot.plan.value()
        if isinstance(snapshots, Acquisition):
            if snapshots.binding != snapshot.binding or snapshots.observed_at != observed_at:
                raise ObservationConflict(
                    "Acquisition does not match this plan, context, phase or observation time"
                )
            inputs = snapshots.snapshots.value()
            result, observations = snapshots.result.value(), snapshots.observations.value()
        else:
            if not isinstance(snapshots, dict):
                raise ValueError("Snapshots must map request IDs to captured responses")
            inputs = FrozenJSON.capture(snapshots).value()
            result, observations = observe(plan, task["context"], inputs, snapshot.phase)
        fingerprint = digest(
            {"revision": revision, "snapshots": inputs, "observed_at": supplied_time, "mode": mode}
        )
        receipt: Receipt = {
            "watch_id": identity,
            "event_id": event_id,
            "revision": revision,
            "observed_at": observed_at,
            "evaluated_at": now,
            "expires_at": observed_at + snapshot.ttl,
            "phase": snapshot.phase,
            "result": result,
            "observations": observations,
            "input": inputs,
            "plan_sha256": snapshot.plan_ref["sha256"],
            "source_evidence": task["_admission"],
            "mode": mode,
        }
        return PreparedObservation(
            identity,
            snapshot.plan_ref["sha256"],
            snapshot.task_ref["sha256"],
            snapshot.phase,
            event_id,
            revision,
            observed_at,
            fingerprint,
            FrozenJSON.capture(receipt),
        )

    def commit(self, prepared: PreparedObservation, *, fence: LeaseFence | None = None):
        if not isinstance(prepared, PreparedObservation):
            raise TypeError("Commit requires an in-process prepared observation")
        if fence is not None and (
            not isinstance(fence, LeaseFence) or fence.watch_id != prepared.watch_id
        ):
            raise ObservationConflict("Lease fence belongs to another watch")
        return self.repository.commit(prepared, fence)

    def submit(
        self,
        identity,
        event_id,
        revision,
        snapshots,
        observed_at=None,
        mode="caller-supplied snapshot",
        *,
        _monitor_token=None,
        _snapshot: WatchSnapshot | None = None,
    ):
        if _snapshot is None:
            self.expire(identity)
        prepared = self.prepare(
            identity, event_id, revision, snapshots, observed_at, mode, snapshot=_snapshot
        )
        fence = LeaseFence(identity, _monitor_token) if _monitor_token is not None else None
        return self.commit(prepared, fence=fence)

    def snapshot(self, identity) -> WatchSnapshot:
        """Read just the immutable execution inputs, without UI projections."""
        self.expire(identity)
        return self.repository.snapshot(identity)

    def expire(self, identity=None):
        return self.repository.expire(identity)

    def get(self, identity):
        self.expire(identity)
        row, task, plan, receipt = self.repository.load(identity)
        selected = phase_plan(plan, row["phase"])
        watch = {
            "id": identity,
            "phase": row["phase"],
            "ttl": row["ttl"],
            "revision": row["revision"],
            "task": task,
            "plan": plan,
            "active_plan": selected,
            "live_available": supports(task["provider"], selected["requests"]),
            "check_evidence": {
                check["id"]: check_details(
                    check,
                    (receipt or {}).get("observations", {}).get(check["request_id"]),
                    task["context"],
                )
                for check in selected["checks"]
            },
            "receipt": receipt,
        }
        watch["explanation"] = explain(watch)
        return watch

    def status(self, identity):
        watch = self.get(identity)
        for key in ("task", "plan", "active_plan"):
            watch.pop(key)
        watch["receipt"] = receipt_summary(watch["receipt"])
        return watch

    def directory(self, after="", limit=30):
        return self.repository.directory(after, limit)

    def usage(self, identity=None):
        return self.repository.usage(identity)

    def record_acquisition(self, identity, network_reads, shared_reads):
        return self.repository.record_acquisition(identity, network_reads, shared_reads)

    def deliveries(self, identity, consumer):
        return self.repository.deliveries(identity, consumer)

    def acknowledge_delivery(self, identity, consumer, message_id):
        return self.repository.acknowledge_delivery(identity, consumer, message_id)

    def outbox(self, identity, after=0, recent=False, compact=False):
        return self.repository.outbox(identity, after, recent, compact)

    def ack(self, identity, message_id):
        return self.repository.ack(identity, message_id)
