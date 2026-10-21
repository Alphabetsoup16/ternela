# Find a wait, leave, and resume it

Ternela's local runtime now joins five existing pieces: connector recipes, saved
watches, polling, evidence receipts and agent delivery. This pass adds no dependency,
hosted service, background model call or transport protocol.

## Refined implementation contract

| Change | User benefit | Boundaries and failure cases |
| --- | --- | --- |
| Connector-owned descriptors | A reviewed recipe appears in the new-watch form without a second frontend catalog. | Presentation metadata never grants access or replaces recipe validation. Unknown/missing arguments still fail before acquisition. |
| Server watch directory | Find watches after losing browser storage or moving to another browser on this host. | Stable ID keyset pagination, 30 items by default, maximum 100. Listing reads task blobs and SQLite decision summaries, never full receipt/plan blobs. Insertions before the current cursor appear after refreshing. |
| Named consumer checkpoints | Disconnect, restart, receive a decision, then acknowledge after processing. | Ordered at-least-once delivery. Checkpoints are independent of other consumers and the UI's legacy acknowledgement. Skipping a pending decision is rejected; duplicate acknowledgement is safe. |
| Shared public reads | Evaluate different waits from one identical HTTP response. | At most 20 watches and 60 seconds per worker batch. Exact host and URL, fixed anonymous GET representation; each watch keeps its plan, evaluation, TTL, receipt, lease and deadline. No reuse across batches or beyond a watch's TTL. |
| Durable usage counters | See acquisition work, decision changes and missing evidence. | Counters start when this feature is installed. Accepted receipt counters update transactionally. Acquisition counts are persisted after collection, including failed/fenced attempts; a crash before that write can undercount. These are diagnostic counters, not billing records. |

Fixed polling intervals remain explicit. Provider backoff, Retry-After and monitor
deadlines retain priority. This pass does not infer that a quiet resource is safe to
check less often; that needs a separately measured detection-latency policy.

## Try a durable handoff

With the service running:

```sh
python3 -m examples.resumable_wait
# Copy the printed watch ID. This process can exit immediately.
python3 -m examples.resumable_wait --watch WATCH_ID
```

The second process prints one pending decision alongside current status, then
acknowledges it for `example-agent`. Run it again to await the next decision. Use a
new `--consumer` name to demonstrate independent delivery. `--base` selects another
local port. A timeout leaves the monitor and checkpoint unchanged.

```python
from ternela import Client

client = Client()
page = client.watches()  # Follow page["next"] using watches(after=...).
watch_id = page["items"][0]["id"]
delivery = client.wait_delivery(watch_id, "my-agent", timeout=120)
message, current = delivery["message"], delivery["current"]
print(message["receipt"]["result"], current["receipt"]["result"])
# Perform your application-specific handling here. A historical ready decision
# can already be expired or invalidated; inspect current status before acting.
# External side effects need an idempotency key: f"{watch_id}:{message['id']}".
client.acknowledge_delivery(watch_id, "my-agent", message["id"])
```

The outbox records decisions, including blocked, unknown and expired evidence.
Receiving a message is not authorization to act. Observation and an external action
are non-atomic, and two simultaneous processes using one consumer can receive the
same message. Application actions must tolerate retries. There are no delivery
leases, callbacks, push delivery or dead-letter routing in this local contract.

## Read sharing and accounting

A batch stores at most 128 raw responses, 512 KiB per response and 4 MiB total.
Only successful responses are eligible; `no-store` and `no-cache` prevent reuse.
Errors are reacquired; no success is carried into a later batch. The conservative
batch start time bounds every shared observation's age. Independent normalization,
selector evaluation and commit fencing prevent one watch's result becoming another
watch's conclusion. Provider-reported facts retain their original limitations.

This cache is exclusively for reviewed anonymous connectors. Authenticated reads
would require authorization-scope isolation before becoming eligible.

`client.usage(id)` returns per-watch counters; `client.usage()` sums them:

| Counter | Meaning |
| --- | --- |
| `observations` | Accepted, nonduplicate observation events. |
| `read_attempts` | Evaluated request/page trace entries, including caller snapshots and simulation; these are not network traffic. |
| `unknown_observations` | Accepted observations that could not establish a reliable decision. |
| `decision_changes` | Outbox insertions, including the first result and expiry transitions. |
| `expiry_events` | Evidence-expiry transitions; do not count as new observations or reads. |
| `network_reads` | Completed acquisition rounds' HTTP transport attempts, successful or failed. |
| `shared_reads` | Reads served from the current public-response batch. |

Historical receipts stay in the existing content-addressed gzip store. Small SQL
counters and consumer cursors need no second event log. No raw provider documents
are copied into a new analytics dataset. Retention/garbage collection, tenant
isolation, billing-grade accounting and authenticated connectors remain separate work.

## Validation

The tests cover migration from an old watch database, browser-independent listing,
pagination, expiry, disk-write failure, duplicate and rejected observations,
consumer reconnect/ack crashes, independent consumers, concurrent acknowledgement,
skipped/foreign acknowledgements, historical success after expiry, bounded waits,
shared responses with different results, pause fencing, TTL changes, cache limits,
cache-control, transport failure and batch expiry. Existing backoff/deadline tests
continue to exercise the same monitor runtime.

See the dated product-runtime audit for measured results. Finite tests establish
these exercised behaviors, not universal correctness or production capacity.
