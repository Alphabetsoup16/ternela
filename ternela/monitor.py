# SPDX-License-Identifier: Apache-2.0
"""Public polling policy; scheduling SQL belongs to ScheduleRepository."""

import math
import time

from .acquisition import ReadBatch, acquire
from .connectors import live_mode, supports
from .contracts import Acquisition, ScheduleClaim
from .errors import LeaseExpired
from .observations import phase_plan
from .schedules import ScheduleRepository


def interval_seconds(value):
    if type(value) not in (int, float) or not 60 <= value <= 86400:
        raise ValueError("Monitor interval must be 60–86400 seconds")
    return value


class Monitors:
    """One host, multiple safe claimers; same public API as the original monitor."""

    lease_seconds = 90

    def __init__(self, engine, collector=None):
        self.engine, self.collector = engine, collector
        self.repository = ScheduleRepository(engine.repository.database)

    def get(self, identity):
        row = self.repository.get(identity)
        if row is None:
            return None
        result = dict(row)
        result.pop("lease_token")
        result["enabled"] = bool(result["enabled"])
        if result["enabled"] and result["deadline"] <= self.engine.now():
            result.update(enabled=False, last_error="deadline_reached")
        return result

    def start(self, identity, interval=300, duration=86400):
        interval_seconds(interval)
        if type(duration) not in (int, float) or not 60 <= duration <= 604800:
            raise ValueError("Monitor duration must be 60–604800 seconds")
        watch = self.engine.get(identity)
        if any(
            requirement["coverage"] != "observable"
            for requirement in watch["active_plan"]["requirements"]
        ):
            raise ValueError("Resolve unverifiable requirements before starting automatic checks")
        requests = phase_plan(watch["plan"], watch["phase"])["requests"]
        provider = watch["task"]["provider"]
        if not supports(provider, requests):
            raise ValueError("No reviewed anonymous connector for these operations")
        provider_key = provider + "-public"
        self.repository.configure(identity, interval, duration, provider_key)
        return self.get(identity)

    def pause(self, identity):
        self.repository.pause(identity)
        return self.get(identity)

    def claim(self):
        return self.repository.claim(self.lease_seconds)

    def run_batch(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("Monitor batch limit must be 1–20")
        batch = ReadBatch()
        count = 0
        while count < limit and time.monotonic() < batch.deadline and self.run_once(_batch=batch):
            count += 1
        return {
            "watches": count,
            "network_reads": batch.network_reads,
            "shared_reads": batch.shared_reads,
        }

    def run_once(self, *, _batch=None):
        """Collect one due watch outside the database lock. Safe after process restart."""
        row = self.claim()
        if row is None:
            return False
        claim = ScheduleClaim.from_mapping(row)
        identity, token = claim.watch_id, claim.lease_token
        snapshot = self.engine.snapshot(identity)
        plan, task = snapshot.plan.value(), snapshot.task.value()
        started = self.engine.now()
        batch = _batch or ReadBatch()
        # Never reuse an earlier watch's evidence beyond this watch's TTL.
        if time.time() - batch.started >= snapshot.ttl:
            batch.cache.clear()
            batch.bytes = 0
            batch.started = time.time()
        previous_reads, previous_shared = batch.network_reads, batch.shared_reads
        try:
            args = (plan, task["context"], snapshot.phase)
            acquired = (
                self.collector(*args)
                if self.collector
                else acquire(*args, provider=task["provider"], batch=batch)
            )
            if isinstance(acquired, Acquisition):
                snapshots, started = acquired.snapshots.value(), acquired.observed_at
            else:
                snapshots, started = acquired
        except Exception:
            # Never keep the old successful decision after a failed acquisition.
            acquired, snapshots = {}, {}
        if not self.collector:
            self.engine.record_acquisition(
                identity, batch.network_reads - previous_reads, batch.shared_reads - previous_shared
            )
        error, retry_after = acquisition_status(snapshots, started)
        if retry_after:
            # Provider hints remain relevant even when the watch was paused during its read.
            self.repository.defer_provider(claim.provider_key, self.engine.now() + retry_after)
        try:
            result = self.engine.submit(
                identity,
                "monitor-" + token,
                claim.revision + 1,
                acquired if isinstance(acquired, Acquisition) else snapshots,
                started,
                live_mode(task["provider"]),
                _monitor_token=token,
                _snapshot=snapshot,
            )
        except LeaseExpired:
            return True
        if not result["accepted"]:
            error = "observation_superseded"
        failures = claim.failures + 1 if error == "provider_unavailable" else 0
        delay = max(
            claim.interval,
            min(3600, claim.interval * 2 ** min(failures, 6)) if failures else 0,
            retry_after,
        )
        now = self.engine.now()
        self.repository.finish(claim, now + delay, failures, error, now)
        return True


def acquisition_status(snapshots, observed_at):
    """Provider failures back off; missing semantic fields remain evaluator decisions."""
    if not isinstance(snapshots, dict):
        return "provider_unavailable", 0
    envelopes = [
        page
        for value in snapshots.values()
        for page in (value if isinstance(value, list) else [value])
    ]
    unavailable = not envelopes
    retry_after = 0
    for page in envelopes:
        if not isinstance(page, dict):
            unavailable = True
            continue
        status = page.get("http_status", 0)
        unavailable |= type(status) is not int or not 200 <= status < 300
        headers = page.get("headers", {})
        if not isinstance(headers, dict):
            continue
        try:
            delay = float(headers.get("retry-after", "0"))
            if headers.get("x-ratelimit-remaining") == "0":
                delay = max(delay, float(headers.get("x-ratelimit-reset", "0")) - observed_at)
            if math.isfinite(delay):
                retry_after = max(retry_after, min(86400, max(0, delay)))
        except (TypeError, ValueError):
            pass
    return "provider_unavailable" if unavailable else None, retry_after
