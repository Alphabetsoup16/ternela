# Evaluation

The release includes six task descriptions, their compiled plans and 41 simulated
behavioral scenarios. Run them against the current runtime with:

```sh
python3 -m ternela.setup_demo
python3 -m ternela.regression
python3 -m ternela.check
```

## Recorded regression result

| Measure | Result |
|---|---:|
| Scenario expectations met | 41 / 41 |
| Structural audit issues | 0 |
| False ready/complete outcomes | 0 in this set |
| Unit/API tests in the current local suite | 355 |

The machine-readable recorded result is in [evaluation.json](evaluation.json).
The synthetic task/probe fixtures are under `expansion_lab/fresh/`; the bundled
data-only plans are in `examples/catalog.json`.

The scenarios cover quantity preservation, payment identity and amount, release
metadata, check-run identity and conclusion, Slack directory state, and a claim
the available API cannot establish. Unit/API tests additionally exercise restarts,
expiry, duplicate retries after supersession, pagination, outbox cursors and
loopback HTTP access controls.

## What broke before it passed

| Iteration | Passing cases | Structural issues | False ready/complete |
|---|---:|---:|---:|
| Initial expansion experiment | 35 / 41 | 8 | 4 |
| Phase-safety repair | 35 / 41 | 0 | 0 |
| Source-backed rerun | 41 / 41 | 0 | 0 |

The first fresh run exposed checks assigned to the wrong business phase. The
repair made failed preservation guards gate readiness. Six Slack cases remained
unverifiable because the pinned user schema was malformed; separately attributed
official example evidence resolved that ingestion gap. Missing runtime fields
still produce unknown.

These later results are **regression evidence**. The tasks, implementation and
evaluation were developed by the same author; the three providers were already
known. Responses are simulated. Do not interpret the passing set as blinded
generalization, proof of all business semantics, or a production reliability SLA.

The workbench also supports public GitHub, PyPI and npm reads without accounts. Live reads are
examples of the data path, not substitutes for a held-out benchmark.

The [release audit](audits/2026-10-19.md) records the original 108-test release,
additional boundary tests, a local
performance measurement and a fresh compilation smoke test. A smoke test is not
a held-out generalization benchmark.

The [local monitoring pass](audits/2026-10-19-monitoring.md) adds failure-injection, SDK, job-recovery and
optional semantic-review contract tests. Those tests use simulated acquisitions and
mock Jev responses, not a semantic-quality benchmark or a provider reliability SLA.
The 41 scenario expectations and their recorded historical result are unchanged.

The [public connector pass](audits/2026-10-19-public-connectors.md) records six live
registry reads, including two 404 responses, and same-state HTTP payload measurements.
The new registry recipes are deterministic reviewed adapters, not evidence of
automatic compiler generalization to new providers.

The [connector contract pass](audits/2026-10-19-connector-contracts.md) adds provider
conformance, extension/recovery and semantic-evaluator mechanics tests. A frozen
14-case Jev corpus is available, but no live Jev evaluation ran because credentials
were unavailable. Mocked evaluator tests do not measure semantic accuracy.

The [GitHub Status experiment](audits/2026-10-19-github-status.md) freezes 55 expectations
before implementation. The first adapter matched 52, exposing a missing-selection
policy gap; the final run matches all 55 with the original labels. These are
implementation-authored pre-implementation expectations, not independent annotation.

The [distribution audit](audits/2026-10-19-distribution-review.md) adds reproduced
update-transaction, command-recovery and installer-isolation faults. Its Docker
probe runs actual candidate processes and data restoration with synthetic release
responses. PowerShell checks use mocked Docker; native Windows and a published
release trial remain separate validation steps.

The [continuous development pass](audits/2026-10-20-development-loop.md) adds 27 fault
tests and exercises incremental selection and offline receipt comparison. Historical
receipt expectations detect behavioral change; they are not independent truth labels.
The recorded timings are local observations, not service performance guarantees.
