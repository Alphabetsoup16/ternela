# Continuous development loop — 2026-10-20

The [design and operating contract](../development.md) turns the existing check command
into one foreground development loop. The [expectations](../experiments/continuous-checks-v1.md)
were written before implementation. No account, paid model, provider write or approval
workflow is involved. The source/UI runtime receives no receipt-harvesting hooks.

## Exercised results

The full suite passes **268 tests**: 173 general, 9 safety and 86 workbench. The 27 new
tests cover selection, conservative fallback, source stability, invalidation, fresh
processes/bytecode, provider boundaries, bounded retention, exact integers, corrupt
or missing evidence, raw normalization, preservation and pagination replay.

A temporary checkout also exercised real source edits and fresh checker subprocesses:

| Change | Result | Internal check time |
|---|---|---:|
| Full validation | 268 tests and 9 historical receipt examples pass | 6.671 s |
| Unchanged tree/corpus | 0 test subprocesses; 9 cached receipt results | 28 ms |
| Localized semantic-review edit | 11 tests in 2 files pass | 726 ms |
| Provider imports a scheduler | Ownership violation fails; baseline retained | 225 ms |
| Existing curated empty-files case submitted through the runtime | New pending example harvested; comparison passes | 767 ms |
| Normalizer forced to report a usable file | Pending-to-complete mismatch and fixed tests fail; corpus/baseline retained | 5.951 s |
| Original validated source restored | Cached success is available again | 29 ms |

These are single observations on this Mac/Python 3.15, not cross-platform benchmarks
or production latency claims. Internal time excludes CLI startup; total wall times
were respectively 6.770 s, 97 ms, 795 ms, 294 ms, 838 ms, 6.022 s and 95 ms.

Nine examples came from a consistent copy of the existing local receipt database;
they include both public reads and caller/simulated snapshots. One additional
negative example reused the existing authored PyPI fixture. None is independent
annotation or evidence of unfamiliar-provider generalization. Original receipts were
not modified. A real finite watcher ran three polls: one initial cached check, one
focused check after an edit, and no child process on the idle poll. Every temporary
source mutation was restored.

The unit tests additionally demonstrate that replay can process two recorded public
pages through the existing connector/pagination runtime without network access, and
that a changed continuation fails even if a status-only comparison might conceal it.
A same-size source edit with its original timestamp and an existing bytecode cache
also exercises the modified source in a fresh test child.

## Storage, boundaries and limits

The corpus retains at most 64 examples, 12 per provider, 256 KiB per example and
4 MiB expanded overall. Deterministic gzip, cumulative exclusion counts and a bounded
SQLite cursor keep repeated ordinary activity from multiplying local examples.
The exercised 10-case corpus is approximately 34 KiB compressed. Original receipts
and curated source fixtures are retained independently. The developer cache is not
part of the application's correctness, health or evidence claims.

Local import analysis is an accelerator with conservative fallback, not a proof of
all Python dependencies. Provider import rules are not a sandbox. Historical results
can be wrong; a disagreement needs interpretation alongside fixed expectations.
Finite sampling and successful removal experiments do not justify deleting rare
recovery code blindly. UI behavior still requires browser checks. No CI, daemon,
package publication, release or remote upload was added.

Compressed logs and the detailed local experiment record are in ignored
`data/audits/continuous-checks-2026-10-20/`. The machine-readable public summary omits
receipt payloads. Earlier audit inventories describe their own recorded snapshots.
