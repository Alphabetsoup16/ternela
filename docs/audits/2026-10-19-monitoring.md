# Local monitoring and developer-experience pass · 19 October 2026

This incremental review starts from commit `f297c646beda6d7b89d3224a290fef2359498fe5`.
It adds a bounded local monitoring workflow and refines the product plan. It is not
an assertion that every defect has been found or that the service is production-ready.

## Delivered

- Persisted public GitHub schedules with expiring leases, indexed due/deadline queries,
  pause/resume, duration limits and fenced receipt commits.
- Provider failures become unknown evidence. Retries back off; provider retry hints
  can pause scheduled public GitHub reads across watches.
- New SDK methods for health, refresh, monitoring, bounded decision waits and compiler
  job waits. Current decisions remain separate from historical queue transitions.
- Completed admitted compiler jobs are recoverable by ID after restart. Failed runs
  remain failed; unfinished runs explicitly report interruption.
- SDK and browser mutations rejected for an old local session refresh the session
  once. Already accepted operations are not automatically retried.
- Structured explanations, accurate TTL display and workbench start/pause controls.
- An optional Jev adapter with three independent task/plan meaning judgments. It is
  advisory, cannot change a decision, and has no automatic acceptance threshold.
- A comprehensive roadmap with user journeys, acceptance criteria, exact PR/head
  semantics, delivery design, prospective evaluation and release prerequisites.

CI workflows and packaging metadata are unchanged. No package, full CLI or service
was published or deployed; changes remain local for review.

## Validation

On Python 3.15.0, **141 unit/API tests** pass, including the previous suite plus
schedule recovery, competing claims, expiry, deadline fencing, paused/configured
reads in flight, provider outages, rate-limit pauses, superseding manual events,
crashes around commit, job recovery, session recovery and Jev contract tests.
No resource warnings remain in the final run. All **41 known simulated regression
scenarios** pass with zero false ready/complete outcomes and zero structural issues.
These counts are local evidence, not a new CI result or a generalization benchmark.

A public GitHub smoke test acquired one release observation, evaluated four checks
and returned complete. An actual local process restart preserved its monitor,
receipt and outbox. The existing SDK client recovered its old session and the
monitor was paused afterward. The documented `examples.public_wait` example also
ran successfully against the final source checkout. These are smoke tests, not a
provider reliability or arbitrary task-understanding benchmark.

The workbench start → automatic observation → complete → pause flow was exercised
against the public GitHub API. Browser controls also recovered after a real service
restart without reloading the already-open page. Desktop and 390-pixel layouts were
inspected; the narrow layout had no horizontal overflow, the new control was 44px
high, and its note used 14px text. The UI detector reported no findings. Python lint,
format checks, JavaScript syntax and local documentation links pass.

**Jev was not called:** no TypeSafe API credential was configured. Its request and
response contracts were checked with explicit mock fixtures. No claim about live
availability, semantic accuracy, latency or cost is made.

## Local timing

30 iterations on this host, Python 3.15.0, synthetic payment evidence and temporary
SQLite/gzip storage. Medians: warm creation 1.810ms; submission 1.453ms; watch read
0.805ms; outbox read 1.030ms. Cold creation was 75.812ms. This is a local sample and
not a throughput test, concurrency limit, monitoring-latency guarantee or comparison
with the earlier Python 3.13 measurements.

## Remaining limits

Public GET monitoring requires the local process to run. One worker handles reads
serially. Sleeping hosts, network failures and provider quotas can delay detection;
TTL expiry still invalidates stale evidence. Retention/garbage collection, an active
watch dashboard, explicit watch cancellation and durable compiler queues remain open.

Authenticated GitHub App access, verified webhooks, PR-required-check resolution,
callbacks, cross-tenant authorization and distributed operation are not implemented.
Monitor pause, monitor deadline and client timeout have distinct documented meanings.
Shared acquisition remains a proposed experiment with authorization and freshness
constraints, not an implemented optimization or established moat.

The machine-readable companion records validation and source hashes. Compressed
provider receipts and smoke logs remain in ignored local data, outside the repository.
