# API and event contract

Start `python3 -m thenwake` from the repository checkout. The service binds to
`127.0.0.1:8765`; its session token is intended for this local prototype.

```python
from thenwake import Client

client = Client()
watch = client.watch("n02")  # Bundled payment example; no provider request.
request_id = watch["plan"]["requests"][0]["id"]

# Explicitly simulated provider evidence.
client.submit(watch["id"], event_id="payment-observation-1", revision=1,
    snapshots={request_id: {
        "http_status": 200,
        "body": {"id": "pi_new", "status": "succeeded", "currency": "usd",
                 "amount_received": 4200, "customer": "cus_new"}
    }})

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
Completed compilations remain in the catalog after a restart. In-flight compiler
jobs are not a restart-resumable distributed queue.

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
  recommendations do not start an automatic provider polling schedule.


## HTTP surface

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/bootstrap` | Local session and bundled task catalog |
| POST | `/api/compile` | Start task compilation |
| GET | `/api/jobs/{id}` | Inspect in-process compilation status |
| POST | `/api/watches` | Create a watch from a template or completed job |
| GET | `/api/watches/{id}` | Read current state, checking evidence expiry |
| POST | `/api/watches/{id}/events` | Submit a complete observation snapshot |
| POST | `/api/watches/{id}/live` | Collect bounded public GitHub evidence |
| GET | `/api/watches/{id}/outbox?after={cursor}` | Read up to 100 historical transitions |
| POST | `/api/watches/{id}/ack` | Acknowledge a transition |

Mutation requests require JSON plus the local `X-Observation-Token` session header.
The server checks Host and Origin and exposes only its explicit static asset routes.
This local interface is not a production authentication or tenant-isolation scheme.

## Local data

The setup command downloads public documentation. Compilations and observations
remain in ignored local `data/`, stored as gzip blobs with SHA-256 verification;
SQLite contains watch metadata and outbox references. A task submitted for compilation
is sent to the configured Codex inference backend. Do not use the local prototype as
a public internet service.
