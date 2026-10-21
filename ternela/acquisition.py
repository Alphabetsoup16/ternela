# SPDX-License-Identifier: Apache-2.0
"""Bounded anonymous reads through reviewed provider adapters."""

import json
import subprocess
import time
from copy import deepcopy

from ternela import connectors
from ternela.connectors import request as connector_request

from .contracts import Acquisition, FrozenJSON, binding_hash
from .observations import run_observations


class ReadBatch:
    """Ephemeral bounded sharing; no reuse across worker batches or failed reads."""

    def __init__(self, reader=None):
        self.reader = reader or connectors.read_json
        self.started = time.time()
        self.deadline = time.monotonic() + 60
        self.cache = {}
        self.bytes = self.network_reads = self.shared_reads = 0

    def read(self, url, host, timeout):
        key = (host, url)
        if time.monotonic() >= self.deadline:
            raise ValueError("Read batch expired")
        if key in self.cache:
            self.shared_reads += 1
            return deepcopy(self.cache[key])
        self.network_reads += 1
        result = self.reader(url, host, min(timeout, max(0.001, self.deadline - time.monotonic())))
        status = result.get("http_status", 0)
        control = str(result.get("headers", {}).get("cache-control", "")).lower()
        if (
            type(status) is int
            and 200 <= status < 300
            and not any(flag in control for flag in ("no-store", "no-cache"))
        ):
            size = len(json.dumps(result, allow_nan=False).encode())
            if (
                len(self.cache) < 128
                and size <= 512 * 1024
                and self.bytes + size <= 4 * 1024 * 1024
            ):
                self.cache[key] = deepcopy(result)
                self.bytes += size
        return result


def acquire(plan, context, phase, provider="github", *, batch=None) -> Acquisition:
    captured, total = {}, 0
    started, budget_started = batch.started if batch else time.time(), time.monotonic()

    def factory(request):
        pages = captured.setdefault(request["id"], [])

        def transport(call):
            nonlocal total
            total += 1
            remaining = 60 - (time.monotonic() - budget_started)
            try:
                if total > 20 or remaining <= 0:
                    raise ValueError("Live read budget exhausted")
                result = (
                    connector_request(provider, call, min(15, remaining), reader=batch.read)
                    if batch
                    else connector_request(provider, call, min(15, remaining))
                )
            except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
                result = {"http_status": 0, "body": None, "error": str(exc)}
            pages.append(deepcopy(result))
            return result

        return transport

    result, observations = run_observations(plan, context, phase, factory)
    return Acquisition(
        binding_hash(plan, context, phase),
        started,
        FrozenJSON.capture(captured),
        FrozenJSON.capture(result),
        FrozenJSON.capture(observations),
    )


def collect(plan, context, phase, provider="github", *, batch=None):
    """Legacy snapshot tuple. Runtime uses acquire() to avoid evaluating it again."""
    acquired = acquire(plan, context, phase, provider, batch=batch)
    return acquired.snapshots.value(), acquired.observed_at
