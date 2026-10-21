# Local product runtime — 2026-10-20

The [refined plan and operating contract](../product-runtime.md) joins connector
metadata, saved watches, named consumer delivery, shared public reads and usage.
All five approved areas were implemented on the existing runtime without new
packages, hosted accounts, provider writes or background model calls.

## Evidence

**294 tests pass** (173 general, 9 safety, 112 workbench), including 26 additional
failure/integration cases. **10/10** harvested historical receipt comparisons pass;
no connector ownership violations or changing-source checks were reported.

| Compatible watches | Independent reads | Shared reads on the wire | Reused responses | Incorrect decisions |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 0 | 0 |
| 10 | 10 | 1 | 9 | 0 |
| 100 | 100 | 5 | 95 | 0 |

This deterministic offline experiment alternates exact Actions/API component
conditions against one frozen public summary. Each condition is evaluated separately.
The batch cap is 20, so 100 watches need five acquisitions. One local run took
443 ms independent versus 564 ms shared at 100 watches: this demonstrates fewer
provider requests, not a faster local evaluator or measured live-network savings.
Raw results are compressed under `data/audits/product-runtime-2026-10-20/` and excluded
from release bundles. Fixed negative fixtures remain the semantic oracle.

A real public wait also ran through the rebuilt container on port 8768:

- One Python process created the watch and exited; a second process received,
  printed and acknowledged the decision through the public SDK example.
- After container restart, acknowledged consumers remained acknowledged and an
  unacknowledged consumer received the same message ID again.
- A client created before restart refreshed its token on a rejected POST and
  successfully acknowledged its message afterward.
- The prior watch remained accessible. Existing state was backed up before a
  same-version development refresh, and the prior image was retained for rollback.
- Automatic updates stayed **off**. The probe's newly created monitor was paused.

Desktop 1280px and mobile 390px layouts were exercised. A live PyPI watch was
created using connector-generated inputs, then recovered through Saved watches
from a fresh browser origin with no quick-start browser memory. No horizontal
overflow was observed. Usage loads on demand. The design detector reported three
spacing warnings on existing border-top sections; computed padding was 14–21px,
so these were documented as false positives rather than changing the layout.

## Scope and remaining work

Delivery is ordered at least once. Historical success can already be invalidated;
`wait_delivery` returns current status beside the message and never acknowledges it.
External action idempotency remains the caller's responsibility. Concurrent processes
sharing one consumer may receive duplicates; no callback service or delivery lease
is claimed.

Read sharing is anonymous and batch-local. Fixed schedules, provider backoff,
deadlines, per-watch TTL and commit fencing remain intact. Adaptive detection policy,
authenticated sharing, retention/garbage collection, tenant authorization and
billing-grade accounting remain future work. Acquisition counters are persisted
after each completed collection and can undercount a crash before that write.

The local container and portable bundle were rebuilt; nothing was published.
Native Windows operation and an actual published GitHub-release upgrade remain
outside this pass. Finite tests are evidence for exercised cases, not a proof of
universal correctness.
