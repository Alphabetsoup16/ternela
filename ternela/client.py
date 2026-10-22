# SPDX-License-Identifier: Apache-2.0
"""Tiny standard-library client for the local prototype API."""

import json
import math
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, *args, **kwargs):
        raise ValueError("Local API redirects are not supported")


class ObservationClient:
    def __init__(self, base_url="http://127.0.0.1:8765"):
        origin = urlsplit(base_url)
        if (
            origin.scheme != "http"
            or origin.hostname not in ["127.0.0.1", "localhost"]
            or origin.username
            or origin.password
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
        ):
            raise ValueError("Prototype client requires a plain loopback HTTP origin")
        origin.port  # Validate the port before any request.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.base = base_url.rstrip("/")
        self.token = self._request("/api/bootstrap?session=1")["token"]

    def _request(self, path, body=None, timeout=90, _refresh_session=True):
        from ternela.transport import decode_body

        deadline = time.monotonic() + timeout
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        headers = {
            "Content-Type": "application/json",
            "Accept-Encoding": "gzip",
            "X-Observation-Token": getattr(self, "token", ""),
        }
        try:
            with self.opener.open(
                Request(self.base + path, data=data, headers=headers), timeout=timeout
            ) as response:
                return json.loads(
                    decode_body(
                        response.read(32 * 1024 * 1024 + 1),
                        response.headers.get("Content-Encoding", ""),
                        32 * 1024 * 1024,
                    )
                )
        except HTTPError as exc:
            with exc:
                try:
                    message = json.loads(
                        decode_body(exc.read(65537), exc.headers.get("Content-Encoding", ""), 65536)
                    ).get("error", exc.reason)
                except (ValueError, AttributeError, OSError, EOFError):
                    message = exc.reason
            if (
                exc.code == 403
                and message == "Invalid local session"
                and body is not None
                and _refresh_session
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Local session refresh exceeded the request timeout"
                    ) from None
                self.token = self._request("/api/bootstrap?session=1", timeout=remaining)["token"]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "Local session refresh exceeded the request timeout"
                    ) from None
                return self._request(path, body, timeout=remaining, _refresh_session=False)
            error = HTTPError(exc.url, exc.code, str(message), exc.headers, None)
            error.close()
            raise error from None

    def connectors(self):
        return self._request("/api/connectors")

    def watches(self, after="", limit=30):
        return self._request(f"/api/watches?after={quote(after, safe='')}&limit={limit}")

    def usage(self, watch_id=None):
        return self._request(
            f"/api/watches/{quote(watch_id, safe='')}/usage"
            if watch_id is not None
            else "/api/usage"
        )

    def deliveries(self, watch_id, consumer):
        """Resume a named consumer's unacknowledged decisions after any restart.

        Historical messages are not current authorization. Read status before acting;
        use (watch_id, message id) as the external action's idempotency key.
        """
        return self._request(
            f"/api/watches/{quote(watch_id, safe='')}/deliveries?consumer={quote(consumer, safe='')}"
        )

    def wait_delivery(self, watch_id, consumer, timeout=120, poll_interval=1):
        """Wait for the next historical decision; return it alongside current status.

        No acknowledgement is sent. Reconnect with the same consumer to retry.
        Observation and any caller action remain non-atomic.
        """
        deadline = time.monotonic() + wait_seconds(timeout, "Timeout")
        wait_seconds(poll_interval, "Poll interval")
        path = f"/api/watches/{quote(watch_id, safe='')}"
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"No delivery for watch {watch_id}; its monitor is unchanged")
            messages = self._request(
                path + "/deliveries?consumer=" + quote(consumer, safe=""),
                timeout=min(90, remaining),
            )
            if messages:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                current = self._request(path + "/status", timeout=min(90, remaining))
                if time.monotonic() < deadline:
                    return {"message": messages[0], "current": current}
            time.sleep(max(0, min(poll_interval, deadline - time.monotonic())))

    def acknowledge_delivery(self, watch_id, consumer, message_id):
        """Advance this consumer only, in order, after its processing has succeeded."""
        return self._request(
            f"/api/watches/{quote(watch_id, safe='')}/delivery-ack",
            {"consumer": consumer, "id": message_id},
        )

    def health(self):
        return self._request("/api/health")

    def watch(self, template_id, ttl=30):
        return self._request("/api/watches", {"template_id": template_id, "ttl": ttl})

    def recipe(self, provider, context, ttl=900):
        """Create a provider's reviewed recipe; the recipe declares its business phase."""
        return self._request("/api/watches", {"recipe": provider, "context": context, "ttl": ttl})

    def release(self, provider, package, version, ttl=900):
        """Create a reviewed exact-version metadata wait without a model or account."""
        return self._request(
            "/api/watches", dict(provider=provider, package=package, version=version, ttl=ttl)
        )

    def status(self, watch_id):
        """Read current checks without transferring raw observations or plans."""
        return self._request(f"/api/watches/{quote(watch_id, safe='')}/status")

    def submit(self, watch_id, event_id, revision, snapshots, observed_at=None):
        return self._request(
            f"/api/watches/{quote(watch_id, safe='')}/events",
            dict(
                event_id=event_id, revision=revision, snapshots=snapshots, observed_at=observed_at
            ),
        )

    def get(self, watch_id):
        return self._request(f"/api/watches/{quote(watch_id, safe='')}")

    def wakeups(self, watch_id, after=0):
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise ValueError("Cursor must be a nonnegative 64-bit integer")
        return self._request(f"/api/watches/{quote(watch_id, safe='')}/outbox?after={after}")

    def acknowledge(self, watch_id, message_id):
        return self._request(f"/api/watches/{quote(watch_id, safe='')}/ack", {"id": message_id})

    def compile(self, task, provider, context):
        return self._request("/api/compile", dict(task=task, provider=provider, context=context))

    def job(self, job_id):
        return self._request(f"/api/jobs/{quote(job_id, safe='')}")

    def watch_compiled(self, job_id, phase="after", ttl=30):
        return self._request("/api/watches", dict(job_id=job_id, phase=phase, ttl=ttl))

    def refresh(self, watch_id):
        """Perform one bounded anonymous provider read; never execute a business action."""
        return self._request(f"/api/watches/{quote(watch_id, safe='')}/live", {})

    def monitor(self, watch_id, interval=300, duration=86400):
        """Persist an opt-in polling schedule, which resumes with the service."""
        return self._request(
            f"/api/watches/{quote(watch_id, safe='')}/monitor",
            {"interval": interval, "duration": duration},
        )

    def pause(self, watch_id):
        """Stop scheduled acquisition; existing evidence still expires normally."""
        return self._request(f"/api/watches/{quote(watch_id, safe='')}/pause", {})

    def wait(self, watch_id, timeout=120, poll_interval=1):
        """Return a current fresh ready/complete watch; never consume its wake-ups."""
        deadline = time.monotonic() + wait_seconds(timeout, "Timeout")
        wait_seconds(poll_interval, "Poll interval")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Watch {watch_id} did not finish; its monitor is unchanged")
            watch = self._request(
                f"/api/watches/{quote(watch_id, safe='')}/status", timeout=min(90, remaining)
            )
            receipt = watch["receipt"]
            if (
                receipt
                and not receipt["result"].get("stale")
                and receipt["result"]["status"] in ("ready", "complete")
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                full = self._request(
                    f"/api/watches/{quote(watch_id, safe='')}", timeout=min(90, remaining)
                )
                current = full["receipt"]
                if (
                    current
                    and not current["result"].get("stale")
                    and current["result"]["status"] in ("ready", "complete")
                ):
                    return full
                watch, receipt = full, current
            if receipt and receipt["result"]["status"] == "unverifiable":
                raise ValueError(
                    f"Watch {watch_id} has unverifiable requirements; inspect its gaps"
                )
            if (watch.get("monitor") or {}).get("last_error") == "deadline_reached":
                raise TimeoutError(f"Monitor for watch {watch_id} reached its deadline")
            time.sleep(max(0, min(poll_interval, deadline - time.monotonic())))

    def wait_job(self, job_id, timeout=1200, poll_interval=1):
        deadline = time.monotonic() + wait_seconds(timeout, "Timeout")
        wait_seconds(poll_interval, "Poll interval")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Compilation {job_id} did not finish; inspect the saved job")
            job = self._request(f"/api/jobs/{quote(job_id, safe='')}", timeout=min(90, remaining))
            if job["status"] == "completed":
                return job
            if job["status"] in ("failed", "interrupted"):
                raise ValueError(f"Compilation {job_id}: {job['error']}")
            time.sleep(max(0, min(poll_interval, deadline - time.monotonic())))


def wait_seconds(value, name):
    try:
        valid = type(value) in (int, float) and value > 0 and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite positive number")
    return value
