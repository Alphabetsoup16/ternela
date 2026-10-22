# Local agent handoff — 2026-10-21

The new [end-to-end example](../agent-demo.md) runs an actual export in a separate
process after the first agent process exits. A Ternela completion delivery triggers
the continuation, which reads the CSV and writes a verified summary. The shared
watch, admission, evidence, persistence and ordered-consumer runtime is unchanged.

## Actual runs

- The reference agent completed on macOS and inside an isolated Colima container:
  three CSV orders, 6,800 cents USD, digest-bound answer and acknowledged completion.
  This agent is deterministic and makes no model calls.
- A signed-in Codex CLI completed two real model turns. Both JSON event streams
  reported the same session UUID. Turn one called `tools.py watch`; turn two called
  `tools.py inspect`, read the CSV and calculated its answer from those bytes.
  The final answer passed independent local verification before acknowledgement.
  This is one successful model run, not a reliability or model-quality benchmark.
- The Docker command exported the CSV, answer, ledger, SQLite state and compressed
  logs before removing its own container and anonymous volume. Runtime networking
  was disabled. The existing managed v0.1.1 service stayed healthy; its volume and
  update preferences were not changed.
- Inspecting the model run after its five-minute TTL created a separate `unknown`
  expiry delivery. The earlier completion was already acknowledged; this later
  event did not invoke another agent. Historical completion is not fresh authority.

## Failure and recovery evidence

The 22 added tests include actual reference and producer subprocesses, detached
producer completion, failed exports, invalid durations, pending/wrong-job/expired
observations, modified/missing/symlink/malformed artifacts, invalid agent answers,
model errors, duplicate producer submissions, competing controllers and recovery
both before and after the result ledger is written. Codex adapter contract tests
mock the CLI; the model run above is separate evidence.

Two implementation gaps were found and repaired during this pass. A failed export
can preserve a pending business decision, creating no new decision-change delivery;
the runner now inspects current persisted evidence while waiting. An interruption
between queue acknowledgement and result persistence could lose the handoff record;
the runner now saves its verified result ledger before acknowledging, then recovers
that acknowledgement without another agent turn.

The initial expiry test incorrectly submitted an old observation after a newer
completion; the runtime correctly rejected that revision. The test was corrected
to advance the injected watch clock past expiry. Its expected refusal to resume
was retained. Another initial helper assumed compact queue receipts contain raw
inputs; it was corrected to read the full watch receipt.

## Checks and limits

- 377 unit/API tests pass: 255 general, 9 safety and 113 workbench.
- 41/41 existing simulated regression scenarios pass, with zero structural issues
  and zero false-ready/complete results in that set.
- Ruff 0.17.1 lint and formatting pass. The shell launcher passes `sh -n`.
- No production connector, task compiler, filesystem watcher, webhook receiver or
  general agent scheduler was added. The explicit local observation contract
  describes caller-supplied process/file evidence; it is not an independently
  authenticated attestation or an HTTP API.
- The local output ledger supports bounded recovery. Arbitrary external actions
  still require their own idempotency; at-least-once delivery is not exactly-once
  execution, and observation and action remain non-atomic.

This feature is source-only after v0.1.1. The existing release tag, archive and
checksums are unchanged. Private model logs, session identifiers and run folders
are excluded from Git and release bundles.
