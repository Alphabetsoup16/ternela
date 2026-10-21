# Continuous development checks: v1 expectations

Frozen before implementation, 2026-10-20. These are implementation-authored
expectations, not independently annotated evidence.

The goal is one foreground development command, not another approval workflow.
`python3 -m ternela.check` must retain full-suite behavior. An optional watch mode
reuses successful source fingerprints, follows local import dependencies, and runs
again only when source or receipt evidence changes. Unknown dependencies, deleted
source, configuration and fixtures select the full suite. Unchanged source must
not start unit-test subprocesses. A failed run must not advance the successful
baseline, and source edits during a run must not be certified by that run.

Provider imports must preserve the existing ownership boundary: no workbench runtime,
schedulers or direct network client imports in a provider. Check the entire provider
directory, including new files. Report violations as ordinary check failures, with
no human approval step. This is a source rule, not a Python sandbox or a general
proof of absence of I/O. Runtime dependency changes are reported and select full
checks; they are never approved or rejected on the user's behalf.

Harvest existing accepted event receipts by a read-only SQLite cursor. Do not add
writes to the application's acquisition/commit path. Replay public observations
through the current connector and evaluator with a supplied offline reader; preserve
request binding, continuation, raw payload and page-order evidence. Caller snapshots
use the ordinary evaluator. Never contact a provider during replay. Missing raw data,
changed requests, unused pages and malformed/corrupt saved data must be visible.
Freshness-only receipts are excluded from this pure observation comparison; existing
expiry/restart/delivery tests remain mandatory when affected.

Expected results are historical observations, not independent truth. Preserve the
first expectation for an existing case; a new implementation must not silently
rewrite it. An intentionally broken normalizer must produce a changed-decision
failure. Keep curated negative contract fixtures separate and runnable.

Use bounded, deterministic gzip retention in ignored local data. Deduplicate timing
and event IDs, preserve useful outcome/type/zero/empty-list distinctions, retain
representation across providers/outcomes, and report limits/skips. Never remove
original receipts or export harvested data to the repository or a remote service.

Validation must cover incremental selection, conservative fallback, unchanged-run
skipping, boundary violations, failed/in-flight baseline handling, finite watch
iterations, receipt deduplication/caps, corrupt corpus, missing/stale evidence,
raw re-normalization, pagination/request drift, exact large integers and offline
execution. Measure narrow/unchanged/full paths on this host and report their scope.
No automatic code deletion, expectation relaxation, CI publication or service is part
of this experiment.
