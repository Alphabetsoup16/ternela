# Public connector and compression audit — 2026-10-19

Added anonymous PyPI/npm exact-release recipes alongside GitHub, consolidated live
and replay observation orchestration, and reduced repeated HTTP payloads. Changes
remain local; CI, distribution and deployment are outside this pass.

## Validation

- **158 unit/API tests pass** on Python 3.15.0: 67 general, 9 safety, 82 workbench.
- **41/41 known simulated regression scenarios pass**, with zero structural issues
  and zero false ready/complete outcomes in that set.
- **6/6 live registry probes match expectations**: PyPI httpx 0.28.1 and packaging
  25.0; npm lodash 4.17.21 and scoped @types/node 22.10.0 complete. Two missing
  versions return HTTP 404 and remain unknown. No accounts, inference or package
  publication are involved.
- Loopback SDK checks create, refresh, start and pause both registry recipes.
  Tests cover identity/version mismatch, empty/yanked files, malformed or missing
  metadata, integrity syntax, disallowed targets, gzip bounds, provider-specific
  retry pauses, compact receipts and a decision changing between compact/full reads.
- Ruff lint/format, JavaScript syntax and Prettier pass. The scoped Impeccable
  detector reports zero findings. Desktop/mobile inspection found no horizontal
  page overflow at 390 px. Full receipt disclosure remains available.

These are deterministic reviewed recipes, not evidence that the natural-language
compiler discovers new providers. Live observations establish only the metadata
claims in [the connector contract](../public-connectors.md). No downloaded package
bytes are checked, and six reads do not establish provider reliability or scale.

## Actual HTTP transfer measurements

Each row compares the same local watch state and one transition. The old access
pattern reads the full watch with duplicated formatted receipt text plus the full
recent outbox. The new pattern reads compact status and compact recent outbox.
Bodies exclude HTTP headers; times are single local samples, not load benchmarks.

| Example | Full pattern, identity | Compact, identity | Compact, negotiated gzip | Reduction from full |
|---|---:|---:|---:|---:|
| PyPI httpx | 98,614 B | 1,817 B | 1,195 B | 98.79% |
| npm lodash | 44,354 B | 1,961 B | 1,249 B | 97.18% |

The larger saving comes from omitting repeated raw metadata, plans and admission
records. Gzip then reduces the summaries further. Responses under 1 KiB remain
identity encoded. The browser loads full server-formatted receipt text only on
disclosure. It still polls the latest 100 compact transitions so acknowledgements
from other consumers remain visible. Durable consumers can use outbox cursors.

## Disk compression and local CPU

The captured PyPI receipt is 27,867 raw bytes. Over 30 in-process samples:

| gzip level | Stored bytes | Median compression time |
|---|---:|---:|
| 3 | 7,188 | 0.059 ms |
| 6 | 6,756 | 0.145 ms |
| 9 | 6,754 | 0.129 ms |

HTTP uses level 3; immutable blobs retain level 9 and SHA-256 verification on reads.
Full provider metadata remains in the compressed receipt. Source/docs stay readable;
probe receipts, downloaded source references and test logs live under ignored
`data/audits/public-connectors/`, with large artifacts gzip-compressed. No active
SQLite database was archived and no existing evidence was deleted.

The 30-iteration synthetic local benchmark measured median create 1.783 ms,
submit 1.499 ms, get 0.799 ms and outbox 1.042 ms. The earlier pass measured
1.810/1.453/0.805/1.030 ms respectively. Differences this small do not establish a
CPU improvement; the demonstrated optimization is payload size. No memory cache
or connection pool was added. Provider acquisition retains bounded curl reads.

## Reproduction and evidence

```sh
python3 -m ternela.check
python3 -m ternela.regression
python3 -m examples.public_release_probe
python3 -m ternela.benchmark --iterations 30
```

[Machine-readable record](2026-10-19-public-connectors.json) includes receipt hashes,
HTTP byte counts, timings and hashes for 106 current source files, excluding
this audit pair. Full receipts use content-addressed gzip blobs under
`data/audits/public-connectors/blobs/`. The reproducible probe measures serialization
separately from the actual HTTP samples above. Recorded sizes precede final UI
wording and SDK race-handling edits; compact fields and compression policy match.

The single background worker can still delay unrelated watches behind a slow
provider. Read coalescing, event-driven invalidation, retention and garbage collection
remain future work. There were no live Jev calls because credentials were unavailable;
its mocked contract tests do not constitute a semantic-quality evaluation.
