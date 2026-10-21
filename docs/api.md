# API and event contract

Start `python3 -m ternela` from the repository checkout. The service binds to
`127.0.0.1:8765`; its session token is intended for this local prototype.

```python
from ternela import Client

client = Client()
watch = client.watch("n02")  # Bundled payment example; no provider request.
request_id = watch["plan"]["requests"][0]["id"]

# Explicitly simulated provider evidence.
client.submit(
    watch["id"],
    event_id="payment-observation-1",
    revision=1,
    snapshots={
        request_id: {
            "http_status": 200,
            "body": {
                "id": "pi_new",
                "status": "succeeded",
                "currency": "usd",
                "amount_received": 4200,
                "customer": "cus_new",
            },
        }
    },
)

for message in client.wakeups(watch["id"]):
    current = client.get(watch["id"])
    print(current["receipt"]["result"])
    client.acknowledge(watch["id"], message["id"])
```

## Compile a task

`client.compile(task, provider, context)` starts a real model compilation using the
signed-in Codex CLI. `client.job(id)` reports status; after it completes,
`client.watch_compiled(id, phase="after")` creates a watch. The workbench provides
the same flow, with public GitHub GETs available for supported repository plans.
`client.wait_job(id)` polls with a timeout and surfaces a failed or interrupted job.
Completed admitted compilations can be looked up after restart; unfinished jobs report
interruption. In-flight compiler jobs are not a restart-resumable distributed queue.

## Monitor and wait

`client.refresh(id)` performs one supported anonymous collection. `client.monitor(id,
interval=300, duration=86400)` persists automatic public GitHub, GitHub Status, PyPI or npm checks.
`client.pause(id)` invalidates acquisition in flight and stops future scheduled reads.
`client.wait(id, timeout=120)` returns a current ready/complete watch, fails early for
unverifiable requirements or an elapsed monitor deadline, and never acknowledges
historical queue messages. Its timeout does not stop a monitor.

Watch reads include `explanation` (reason, summary, next step, counts and blockers)
and `monitor` (schedule, deadline, attempts and acquisition errors). A successful HTTP
acquisition is different from a completed task. `client.health()` describes local
capabilities; compiler availability means the executable exists, not that login or
inference was verified. The SDK refreshes a rejected local session once after a
service restart; it does not retry an already accepted mutation. The workbench uses
the same one-time session recovery for rejected mutations.

`client.recipe("github_status", {"component_id": "br0l2tvcx85d"})` creates a reviewed
before-action readiness watch without inference or an account. `client.recipe` also
accepts the existing package recipes with `{"package": ..., "version": ...}`. The
registered builder declares one business phase; this route does not compile arbitrary
natural-language tasks or execute caller-supplied code. The request body is
`{"recipe": provider, "context": arguments, "ttl": 900}` at `POST /api/watches`.
[Status readiness semantics](status-readiness.md).

`client.release("pypi", "httpx", "0.28.1")` or `client.release("npm", "lodash", "4.17.21")`
creates an exact-version metadata recipe without inference or a provider account.
See [public connectors](public-connectors.md) for its evidence contract.

`client.status(id)` omits task, plan, raw input, observations and admission records.
It retains current check details, explanation, freshness, revision, monitor state and
a receipt summary. `client.wait` polls this endpoint, then fetches and rechecks the
full watch before returning. The existing `client.get` and wake-up methods still
return full records.

See [monitoring](monitoring.md) for ordering, TTL and recovery semantics.

## Event contract

- Events contain a complete snapshot of observations, keyed by compiled request ID.
  Each value is an HTTP envelope or an ordered list of page envelopes. Missing
  observations become unknown; earlier successful values are never silently reused.
- Each watch has one caller-managed revision sequence. Event IDs deduplicate retries;
  the same ID with different content is rejected. Lower revisions or observation
  times older than the current snapshot are retained as ignored events.
- Observation times and revision ordering are caller assertions for supplied
  snapshots. This is not verified webhook ingress or cross-provider causal ordering.
- A duplicate returns the current receipt in `receipt` and its immutable original
  event receipt separately in `delivery_receipt`. Expired or superseded successes
  cannot be mistaken for the current decision through that retry response.
- Wake-ups are retained historical transitions, including invalidation by expiry.
  Consumers read the current watch before acting. `wakeups(id, after=cursor)` returns
  up to 100 messages; acknowledgement is durable and idempotent. Delivery is a
  consumer polling contract, not an exactly-once claim.
- Freshness is a caller-selected watch policy. It is not proof of real-world truth
  or atomicity between a read and the consumer's later action. Compiler poll
  recommendations do not start a schedule; monitoring requires explicit opt-in.


## HTTP surface

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/bootstrap` | Local session and bundled task catalog |
| GET | `/api/health` | Capability and setup presence checks; no model inference |
| POST | `/api/compile` | Start task compilation |
| GET | `/api/jobs/{id}` | Inspect in-process compilation status |
| POST | `/api/watches` | Create from a template, completed job or reviewed provider recipe |
| GET | `/api/watches/{id}` | Read current state, checking evidence expiry |
| GET | `/api/watches/{id}/status` | Current decision and checks without raw observations or plans |
| POST | `/api/watches/{id}/events` | Submit a complete observation snapshot |
| GET | `/api/watches/{id}/receipt` | Download the current receipt as exact JSON |
| POST | `/api/watches/{id}/live` | Collect bounded anonymous provider evidence |
| POST | `/api/watches/{id}/monitor` | Start/resume supported public polling with interval and duration |
| POST | `/api/watches/{id}/pause` | Stop scheduled acquisition and fence its in-flight read |
| GET | `/api/watches/{id}/outbox?after={cursor}` | Read up to 100 historical transitions |
| POST | `/api/watches/{id}/ack` | Acknowledge a transition |

Add `compact=1` to the outbox endpoint for receipt summaries. Both representations
support `after` cursors and the same acknowledgements. The UI polls compact status
and the latest 100 compact wake-ups; repeated reads observe acknowledgements from
other consumers. Full receipt text is fetched on disclosure, with exact integers
formatted by the server.

JSON and static responses negotiate gzip above 1 KiB when it saves bytes; explicit
`gzip;q=0` is respected. Responses send `Vary: Accept-Encoding`. The SDK accepts gzip
with bounded decompression (32 MiB); provider acquisition caps both wire and decoded
bodies at 2 MiB. No redirects are followed.

Mutation requests require JSON plus the local `X-Observation-Token` session header.
The server checks Host and Origin and exposes only its explicit static asset routes.
This local interface is not a production authentication or tenant-isolation scheme.

## Local data

The setup command downloads public documentation. Compilations and observations
remain in ignored local `data/`, stored as gzip blobs with SHA-256 verification;
SQLite contains watch metadata and outbox references. A task submitted for compilation
is sent to the configured Codex inference backend. Do not use the local prototype as
a public internet service.

The workbench formats observed values on the server, including large integers.
Its snapshot editor rejects integers outside JavaScript's safe integer range; use
the Python SDK for those values. Receipt downloads preserve the server's JSON bytes.


## Saved watches, connector metadata and delivery checkpoints

| Endpoint | SDK | Contract |
| --- | --- | --- |
| `GET /api/connectors` | `connectors()` | Registry-owned labels, limitations, docs and recipe inputs; also included in bootstrap. |
| `GET /api/watches?after=ID&limit=30` | `watches(after="", limit=30)` | Bounded summaries and `next` cursor, ordered by stable watch ID. |
| `GET /api/usage` | `usage()` | Aggregate diagnostic counters since feature installation. |
| `GET /api/watches/ID/usage` | `usage(id)` | Per-watch counters. |
| `GET /api/watches/ID/deliveries?consumer=NAME` | `deliveries(id, name)` | Up to 100 pending compact decisions after this consumer's checkpoint. |
| `POST /api/watches/ID/delivery-ack` | `acknowledge_delivery(id, name, message_id)` | JSON `{ "consumer": "NAME", "id": 123 }`; acknowledge earliest pending message, idempotently. |

`wait_delivery(id, name, timeout=120, poll_interval=1)` waits for one pending message
and returns `{ "message": ..., "current": ... }`, without acknowledgement. Its
timeout leaves the monitor running. Consumer names allow 1–64 ASCII letters,
digits, underscores, dots and hyphens. Each name has an independent durable cursor;
legacy `acknowledge` continues to annotate UI activity without moving these cursors.

Delivery is at least once. Historical ready/complete messages may already be stale
or invalidated: inspect current status, use the stable watch/message identity for
idempotent external actions, and acknowledge only after handling. There is no atomic
transaction spanning provider observation and your action. All routes retain the
existing single-host trust boundary; there are no tenant-specific permissions.
[Full runtime and accounting contract](product-runtime.md).


## Runtime and format compatibility

`ternela.Client` now comes from `ternela.client`; `workbench.client` remains a module
alias. Public watch/receipt JSON and existing SDK calls retain their shapes.
`GET /api/health` adds `formats` with supported database, plan and receipt versions.
New stored plan/receipt references carry `record_type` and `record_version` metadata;
legacy unversioned records remain readable without rewriting their blobs. Unsupported
explicit versions fail closed. Some errors add a stable `code` beside the existing
`error` text. Specific Python domain errors preserve ValueError/KeyError ancestry.

The internal prepare/commit API is for trusted Python callers. No HTTP route accepts
serialized prepared decisions. See [architecture](architecture.md) for identity,
revision, lease and freshness checks that remain authoritative at commit.
