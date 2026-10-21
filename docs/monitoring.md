# Automatic public API monitoring

Start the local workbench from the source checkout after [setup](../README.md#try-it):

```sh
python3 -m ternela
```

Choose the GitHub, GitHub Status, PyPI or npm public example, then **Start automatic checks**. The
workbench uses a 5-minute interval and a 24-hour monitor duration. Save the watch URL
to return to it; switching tasks creates another watch and does not pause the first.
The local service must be running. A sleeping computer or stopped process cannot
collect observations; persisted schedules become eligible when it runs again.

The same flow through the SDK:

```python
from ternela import Client

client = Client()
print(client.health())
watch = client.watch("live-github-demo-1", ttl=900)
client.monitor(watch["id"], interval=300, duration=1800)
try:
    current = client.wait(watch["id"], timeout=120)
    print(current["explanation"])
finally:
    client.pause(watch["id"])
```

This observes the bundled public GitHub release task. It uses no provider credential
or model call. `python3 -m examples.public_wait` runs a complete version of the example.
The selected API can establish its listed metadata checks; it does not prove that
release artifacts are correct or that a deployment succeeded.

## Four different clocks

| Setting | Meaning |
|---|---|
| `ttl` | How long a captured observation may support the decision; 1–86400 seconds |
| `interval` | Earliest normal time between attempts for this monitor; 60–86400 seconds |
| `duration` | Monitor lifetime from start/resume; 60–604800 seconds; default 86400 |
| `wait(timeout=...)` | How long this SDK call waits; it does not pause the monitor |

Use a TTL comfortably above the interval plus read latency when appropriate for
the task. A shorter TTL is valid but can produce unknown periods between reads.
Backoff, queueing or provider outages may exceed any chosen freshness window.
An unchanged start request is idempotent and preserves the deadline. Resuming a
paused/expired monitor or changing its interval/duration sets a new deadline.

Pause stops new acquisition and invalidates a read still in flight. It does not
delete the watch, invalidate still-fresh evidence or acknowledge queue messages.
Reaching the monitor deadline also prevents in-flight work from committing. Existing
evidence expires normally. Watch cancellation is a separate, future lifecycle feature.

## Recovery and ordering

Schedules and retry state live in the watch SQLite database. Each attempt obtains a
90-second lease; provider collection has a 60-second total read budget and a 20-request
cap. A paused, superseded, expired or replaced lease cannot commit its observation.
After a crash, an expired lease can be reclaimed. Network reads may be repeated.

A receipt and a changed-decision outbox entry remain one database transaction. A
crash after that commit but before releasing the lease can cause another observation,
but unchanged decisions do not create another transition. This is not exactly-once
provider acquisition or exactly-once agent execution.

An attempt uses the revision it claimed before reading. A newer manual event wins
over the older in-flight attempt. Provider failures produce unknown receipts rather
than reusing the last success. Missing API fields remain evaluator uncertainty, even
when the HTTP request succeeded. `last_success` means acquisition passed the connector contract, not that the task's
completion conditions passed. Registry view-contract failures count as acquisition failures.

Failures use bounded exponential backoff. Numeric `Retry-After` and exhausted GitHub
rate-limit reset timestamps impose a provider-specific pause on scheduled reads,
capped at 24 hours. They do not coordinate other clients or the manual refresh button.
This is not a quota allocator: multiple monitors can exhaust the public API quota.

## Limits

Admitted public GitHub repository GET plans and reviewed exact PyPI/npm release
recipes, plus the reviewed GitHub Status readiness recipe, are eligible.
[Status readiness](status-readiness.md). [Registry semantics and examples](public-connectors.md). Stripe and Slack
remain simulated or caller-supplied observations. No account is connected, private
repositories are unsupported, and there is no webhook ingress or outbound callback.
Resolve partial, missing-input or unobservable requirements before starting a monitor;
repeating reads cannot repair a plan that already cannot verify the task.

One background worker processes due monitors serially on one host. Provider latency
can delay other monitors; the expiry sweeper remains separate. No detection-latency
SLA is established. Retention and automatic blob garbage collection are not yet
implemented; use bounded durations and inspect local disk use for longer experiments.

Compilation is separate from monitoring. Completed admitted jobs can be looked up
after restart; unfinished jobs report interruption and need a new compilation.
`client.wait_job(id)` handles polling and reports failures without silently retrying
paid inference. `client.wait(id)` reads current decisions and never consumes or
acknowledges historical wake-ups. Use the outbox API for durable consumer delivery.
