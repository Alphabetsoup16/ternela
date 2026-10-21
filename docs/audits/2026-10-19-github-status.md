# GitHub Status readiness experiment — 2026-10-19

Selected an anonymous public status API rather than another package registry. The
recipe requires one exact leaf component to be reported operational, with no active
incident or maintenance attached. It returns before-action ready/blocked/unknown.

## Freeze before implementation

The [specification](../experiments/github-status-v1.md) and **55 expected decisions**
were frozen before `ternela/providers/github_status.py` existed. The corpus manifest
records raw/compressed hashes, UTC freeze time, baseline code hashes and source
snapshots. A separate test lock records the initial tests and red-run hash. Labels
are implementation-authored expectations, not independent human annotations.

The corpus raw SHA-256 is `dc3e5f49050755c255d13929f28c19e2bfacf8d9744341cc801ff41f8c83be1c`. Its bytes, manifest and expected
labels remain unchanged. It occupies 1,773 compressed bytes from
38,544 raw bytes. There was no post-hoc relabeling to make the adapter pass.

## What failed

The first adapter matched **52/55 cases**. Three cases expected unknown but received
blocked: missing target component, empty component list, and selecting a group rather
than a leaf. None of these three failures was a false-ready outcome; they exposed an
incorrect distinction between known unmet conditions and unavailable resource evidence.

The existing `single` selection mode intentionally treats zero matches as false. It
could not express the stricter semantics this recipe needs. Added the explicit
`single_or_unknown` mode in the v2 schema and evaluator; zero or multiple matches now
remain unknown when that mode is selected. Direct tests preserve legacy `single`
behavior, and all known regression scenarios still pass.

The public creation interface also assumed package/version arguments. Added generic
reviewed-recipe dispatch and `client.recipe(provider, context)`, using only builders
on registered connectors and inferring their one declared business phase. Unsupported
providers and missing/extra arguments fail before acquisition. Existing release calls
remain supported.

A separate initial ordering test incorrectly expected an ignored-event response to
include the current receipt. It was corrected to inspect `engine.get`, as the existing
contract requires. That test-helper correction and the added selector-compatibility
test changed the test file hash; none of the 55 expected decisions changed. The
original failure logs are retained.

## What held

Provider acquisition, raw evidence retention, typed local views, phase/dependency
execution, guards, watch persistence, monitor leases/deadlines/backoff, transport,
expiry and the transactional outbox needed no changes. The adapter uses the existing
one-page Link traversal policy for the summary's nested component list. An unexpected
next link remains incomplete; no fake cursor or has-more metadata was introduced.

Injected lifecycle tests cover ready → blocked → unavailable → restart → recovery →
expiry, durable acknowledgement, older revisions and pausing a read in flight.

## Final evidence

- **55/55 frozen cases pass**, with one identical plan across every case.
- **184 unit/API tests pass** on Python 3.15.0: 91 general, 9 safety, 84 workbench.
- **41/41 known simulated regression scenarios pass**, with zero structural issues
  and zero false ready/complete outcomes in that existing set.
- Three live summary reads return HTTP 200. Actions and API Requests report ready;
  an absent component control reports unknown. These are wiring observations, not
  induced live failure transitions or a guarantee of service health.
- The real workbench shows a ready receipt and four passing evidence checks. Desktop
  and mobile inspection found no page overflow at 390px and no visible error. The
  scoped UI detector reports zero findings. Ruff, JavaScript syntax and Prettier pass.

## Use and reproduce

```python
from ternela import Client

client = Client()
watch = client.recipe("github_status", {"component_id": "br0l2tvcx85d"})
client.refresh(watch["id"])
print(client.status(watch["id"])["explanation"])
```

```sh
python3 -m ternela.check
python3 -m ternela.regression
python3 -m examples.status_probe
python3 -m examples.status_wait  # Requires the local workbench to be running
```

[User contract](../status-readiness.md) · [Machine-readable audit](2026-10-19-github-status.json).
The record includes 133 source hashes, excluding this audit pair. Full receipts,
source snapshots and compressed red/green logs are in ignored
`data/audits/github-status/`. The source snapshot and expected-case artifacts were
captured before implementation; later live receipts retain their actual acquisition
metadata and adapter provenance. No existing evidence was deleted.

This experiment found two concrete abstraction gaps and fixed them. It is evidence
that the expanded boundary handles this domain; it is not evidence that every future
connector will fit without changes. Vendor reporting/cache delay, conservative
uncertainty propagation and leaf-only scope remain explicit limitations. No paid
model, account, CI deployment or provider mutation was used.
