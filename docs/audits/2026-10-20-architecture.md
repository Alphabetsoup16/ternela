# Runtime architecture consolidation — 2026-10-20

The four changes in the architecture review are implemented. The canonical
[architecture contract](../architecture.md) describes ownership and compatibility.

## Results

- Acquisition, caller observations, monitoring, manual refresh and the release updater
  use one prepare/commit path. Live acquisition is evaluated once; preparation is
  outside the database write lock. Commit retains identity, revision, freshness,
  pause, lease/deadline and atomic receipt/outbox/usage checks.
- Watch and schedule repositories own SQL. The watch application service and monitor
  contain domain/orchestration policy and delegate persistence.
- Stable SDK, storage, sources, schema, comparison, evaluation and guard implementations
  moved into `ternela`. Historical imports alias those same objects; intentionally
  different research evaluators remain in their experiment packages.
- Immutable typed handoffs, specific compatible errors, ordered transactional database
  migrations and explicit plan/receipt reference versions protect extension boundaries.

The suite includes migration rollback, concurrent startup, unknown future formats,
legacy imports and blobs, caller mutation, hostile serialized decisions, evaluation
counts, SQL lock availability, stale prepared work, lease/deadline expiry during
blob publication and consumer recovery. The full test and frozen semantic regression
results are retained in compressed local audit evidence.

## Local performance

| Watches | Unrelated payload | Before median | After median | Evaluations before → after |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 0 B | 44.659 ms | 49.627 ms | 20 → 10 |
| 100 | 0 B | 444.284 ms | 460.446 ms | 200 → 100 |
| 10 | 65536 B | 61.336 ms | 54.269 ms | 20 → 10 |
| 100 | 65536 B | 554.964 ms | 527.979 ms | 200 → 100 |

The comparison uses the source snapshot from before this refactor, the same frozen
provider summary, alternating exact component conditions, three trials per workload
and isolated temporary databases. Each case retained the expected ready/blocked
decisions. Public read sharing remains one acquisition per batch of up to 20 watches.

Evaluation counts are halved. Local end-to-end timings are mixed: the 100-watch small
case is slightly slower, while the large-response case is faster. These measurements
do not establish a universal latency improvement, production throughput or network
savings beyond the already tested shared-read behavior.

The first typed implementation was materially slower. Profiling identified redundant
SQLite reads and repeated JSON serialization/validation. Reusing a content-checked
execution snapshot and freezing JSON containers once removed those costs. All
intermediate results remain in ignored `data/audits/architecture-2026-10-20/`.

## Persistence and release scope

New references add kind/version metadata without changing the underlying content
hash. Legacy unversioned references remain readable and old blobs are not rewritten.
Database upgrades have consecutive history and roll back their DDL/version on failure.
Unknown newer formats are rejected before interpretation. In-process handoffs are
not authentication capabilities, and there is no HTTP prepared-decision route.

The container migration/restart smoke evidence is stored separately with the local
audit. The running demo retains its original watches, checkpoints and update preference.
A compressed state snapshot and the previous image are retained for rollback.

No new dependency, model call, public release, authenticated connector or distributed
queue was introduced. Native Windows operation and standalone wheel installation
remain unverified.


## Final verification and deployment constraint

**335 tests pass on macOS and on native Linux storage**, plus 41/41 frozen
semantic cases with zero false ready/complete results. macOS also passes 10/10
historical receipt comparisons. The native Linux run used an isolated, read-only,
network-disabled container with executable test-only tmpfs and no host data writes.

The existing demo migrated schema 0 → 3 and preserved all 12 watches, 3 consumer
checkpoints and 8 queued messages. A legacy watch refreshed into a versioned receipt;
a new scheduled handoff survived restart and explicit acknowledgement. The probe
monitor was paused and automatic updates stayed off.

The full suite on the current Mac Docker **folder mount does not pass**. `/tmp` is
noexec, so executable test fixtures were moved to the existing application volume.
That exposed a transient blob visibility failure, inherited runtime variables in
the installer process, and ineffective advisory locking on the mounted volume.
The storage put path now republishes identical content once after a missing read;
visible corruption and permanently missing files still raise errors. Both launchers
clear all eight supported inherited settings. The supervisor test now exercises
actual separate processes rather than assuming same-process lock semantics.

A separate probe confirmed that both `flock` and `lockf` block a competitor on native
Linux tmpfs, while both allow competing processes on this Mac's current folder mount.
This is a live-storage limitation; no test expectation was weakened or skipped to
obtain a pass. The failed logs and reports are retained in compressed audit evidence.
No actual database corruption was observed, but cross-process SQLite/supervisor
safety cannot be claimed on that mount.

**Follow-up:** the user approved local Docker-managed storage. The demo has now
migrated to an owned native volume; competing-process locks, SQLite exclusion,
public delivery/restart and backup/restore checks pass there. The expanded suite
passes 350 tests on macOS and that volume. See the [storage audit](2026-10-20-storage.md)
for migration evidence and retained limitations. The failed bind-mount results above
remain part of the audit; they were not relabeled as passing.
