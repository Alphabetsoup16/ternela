# Add a connector without changing the runtime

A connector owns provider interpretation: an approved API origin, supported GET
operations, parameter normalization and an optional evidence view. The runtime owns
phase/dependency execution, pagination, preservation guards, schedules, freshness,
receipts and durable delivery. A provider module must not implement its own scheduler
or decide whether an agent should resume.

## Start from the closest shape

| Shape | Example | Provider-specific work |
|---|---|---|
| Raw public API reads | `ternela/providers/github.py` | Fixed host and operation boundary; existing pinned upstream schema |
| Exact package-release wait | `ternela/providers/pypi.py`, `npm.py` | Package/version validation, response normalization, extra evidence conditions |
| Other normalized evidence | `ternela/providers/github_status.py` | `contract()`, `normalize()` and an explicit `recipe(...)` task/plan builder |

`PublicConnector` permits GETs only. Query parameters and continuation URLs are
opt-in capabilities. Both the common connector layer and HTTP transport check the
origin; a continuation must keep the same resource path. Path values are encoded,
unknown/nonempty body or header bindings are rejected, redirects are not followed, and response sizes are
bounded. These are reviewed anonymous adapters. Authenticated APIs, arbitrary HTTP
URLs, webhooks and third-party plugin loading require separate designs.

## The three changes for another release connector

1. Add a provider module extending `ReleaseConnector`. Implement `package_name`
   and `normalize`; override `version_name` if necessary. Declare `purpose` and
   immutable `EvidenceField` conditions. The common recipe supplies exact name and
   version checks, plan shape, schema projection and context binding. Keep raw
   upstream-to-view mapping explicit. Use `PyPIConnector` as a small complete example.
2. Import and add one configured instance to `BUILTINS` in
   `ternela/providers/__init__.py`. Choose a fixed host and documentation URL.
   Duplicate provider IDs are rejected. Registry mappings and instance descriptors
   are immutable. Restart the service after source changes.
3. Add a success payload and explicit negative expectations to
   `tests/fixtures/connector_contracts.json`. Expected decisions must be specified
   separately from the implementation's normalized output. The coverage test fails
   if a registered release connector has no fixture. Include wrong identity/version,
   missing fields, invalid types and a provider-specific unmet condition.

No engine, monitor, receipt, HTTP routing or SDK edits are needed for another
exact-release connector. `client.release(provider, package, version)` dispatches
through the registry. Adding an optional UI preset is a separate presentation choice.
The compiler selector remains deliberately limited to its existing supported sources.

For another domain, an optional `recipe(...)` method can return `(task, plan)` with
one explicit business phase. `client.recipe(provider, context)` dispatches only to
such reviewed builders on registered connectors; additional providers need no new
public route. Existing `client.release` calls remain supported. The generic route
was added after the [status experiment](audits/2026-10-19-github-status.md) exposed
that the initial public interface assumed package/version arguments.

Use `single_or_unknown` when a selected resource must be observed exactly once before
its fields can be evaluated. Missing or ambiguous selection remains unknown. The
legacy `single` behavior is unchanged. This explicit policy was another gap found
by the status experiment; absence does not have the same meaning for every task.

## A normalized connector contract

`contract()` returns an OpenAPI-shaped **local evidence-view schema**, or `None`
when the adapter preserves an existing pinned upstream representation. For local
views, declare each checkable field and response shape. Local normalized contracts
use the existing strict schema subset: inline object/array/scalar types, exact object
keys, enums and bounds. They are checked after normalization, before evaluation;
this is not a general OpenAPI/JSON Schema validator. Keep local-view operations exact. For raw views, add the real
pinned source through the existing source-ingestion workflow; registration alone
cannot manufacture an authoritative schema.

`normalize(body)` must be deterministic and must not mutate `body`. Do not make
network calls, run models, read credentials or produce completion decisions here.
Raise a data/shape error when evidence is absent or malformed. Return a typed false
or zero only when the observed response actually establishes that value. For example,
an observed empty file list is different from a missing file list.

The common acquisition layer retains `source_body`, the normalized `body`, URL and
headers. Invalid views preserve the original HTTP status in `source_http_status`
and become unknown. Admission provenance hashes the contract and adapter source
inventory, including all registered provider modules. These hashes identify local
code and evidence; they are not signatures from the provider or proof of reality.

This is trusted, reviewed Python code. A connector is not sandboxed merely because
it implements these methods. The contract suite and source review are both required.

## Test the boundary, not only the happy path

```sh
python3 -m ternela.check
python3 -m ternela.regression
python3 -m examples.public_release_probe
```

The shared suite admits real recipe shapes, checks raw evidence retention, rejects
wrong identities and versions, exercises malformed roots and HTTP failures, and
blocks unsupported destinations before transport. Existing pagination and scheduling
fault tests run alongside it. Provider-specific fixtures add the semantics that a
shared schema cannot infer.

An extension test registers an in-memory test-only connector and exercises watch
creation, scheduling, outage, restart, recovery, expiry, outbox delivery and
acknowledgement through the existing runtime. No endpoint for that test connector
is contacted. Another test passes an intentionally broken normalizer through the
same negative-evidence oracle and confirms it is rejected.

For an unfamiliar workflow, freeze expected semantics before implementation, obtain
independent review, and test live provider responses separately from mocked faults.
Passing a finite set of examples establishes confidence within those cases, not
universal correctness. A new domain still requires careful evidence modeling.


## Expose a reviewed recipe in the workbench

Override `descriptor()` on the connector to return `provider`, `label`,
`documentation`, `limitation`, and a `recipe` with `label`, `hint` and `fields`.
Fields declare `key`, `label`, and either an `options` value/label map or a
`placeholder` plus `max_length`. Return new dictionaries on each call. The
workbench builds its native form from this metadata; no second JavaScript recipe
catalog is needed. `GET /api/connectors` is the live reference for tools and docs.
`recipe()` still performs every business and input check server-side.

Public acquisition can share an identical raw response within one bounded polling
batch. The connector still normalizes and evaluates independently for each watch.
Do not add authentication to this path: private evidence needs authorization-scope
isolation before it can share reads. [Runtime contract](product-runtime.md).


The stable execution modules now live under `ternela/`. The monitor, manual HTTP
refresh and release updater pass typed acquisitions into one prepare/commit path;
a new connector must work through that path without adding SQL or another evaluator.
Legacy `workbench` and source/storage imports remain aliases for compatibility.
