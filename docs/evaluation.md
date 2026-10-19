# Evaluation

The release includes six task descriptions, their compiled plans and 41 simulated
behavioral scenarios. Run them against the current runtime with:

```sh
python3 -m thenwake.setup_demo
python3 -m thenwake.regression
python3 -m thenwake.check
```

## Recorded regression result

| Measure | Result |
|---|---:|
| Scenario expectations met | 41 / 41 |
| Structural audit issues | 0 |
| False ready/complete outcomes | 0 in this set |
| Unit/API tests | 86 |

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

The workbench also supports a real public GitHub release read. Live reads are
examples of the data path, not substitutes for a held-out benchmark.
