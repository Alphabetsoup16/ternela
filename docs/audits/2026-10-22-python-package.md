# Python package and runtime consolidation

Unreleased working-tree changes following v0.1.2. No GitHub release, PyPI upload or
managed-install upgrade was performed. Artifact filenames still carry the current
0.1.2 version; assign the next version before publication.

## Result

A 1.92 MB pure-Python wheel and 1.89 MB source distribution build with setuptools.
The ordinary `python -m build` path builds the wheel from the source distribution,
so a missing checkout-only asset cannot silently pass. The wheel installs offline
with no runtime Python dependencies and supplies `ternela` / `python -m ternela`,
the UI, reference-agent demo, compressed evidence and upstream notices.

`ternela.paths` owns immutable resources and the default/explicit data location.
Services and the updater derive writable paths from their `Store` rather than
independently reconstructing them. Docker bundles and wheels share the verified
public seed allowlist; generated build assets have no second maintained source copy.
The application bundle remains accepted by the previous immutable launcher verifier.

SDK initialization/session refresh uses the existing bootstrap endpoint's compact
query form. Older servers ignore the query and still return a compatible token.
The full UI response remains available. Watch reads reuse one repository connection
for expiry and loading, preserving transactional expiry and durable delivery.
The base import defers HTTP-client imports until `Client` is requested.

## Validation

- **391 unit/API tests**, **41/41 known regression scenarios**, ten local receipt
  examples, Ruff lint/format, JavaScript syntax and whitespace checks pass.
- The same wheel passed the installed-artifact probe on macOS/Python 3.15 and Linux
  containers with Python 3.11 and 3.15. Container probes had no external network,
  read-only root filesystems, temporary native storage and no persistent volumes.
- Each probe installed outside the checkout with `--no-index --no-deps`, verified
  seeded sources, ran deterministic replay, imported compiler resources, exercised
  the actual public SDK and UI/API assets, and completed the real process/artifact
  handoff with a verified 6,800-cent answer. Installed source files stayed read-only
  and unchanged; command-selected state stayed outside the installed code.
- New regression cases cover state location, seed fallback, visible corrupt overrides,
  supplement writes without changing the seed, and expiry/delivery after restart.
  Existing fault cases cover HTTP failures, leases, races, migrations and recovery.
- A full run exposed a stale test mock that redirected only part of updater storage.
  Consolidating paths under `Store.root` restored isolation. Its one development-only
  record was archived and removed; all 11 pre-existing development watches were
  retained unchanged. The managed installation was untouched. Failure receipts remain
  in the private compressed audit logs.

## Local measurements

Four alternating before/after pairs, 60 operations per run, against released commit
`effc44f2574213e9c044b90e5a2397b2584598bc`. Each process used synthetic evidence,
temporary SQLite/gzip storage, and no provider or model requests. Values below are
medians of each run's medians; import values use 12 fresh processes per version.

| Measurement | Before | After |
|---|---:|---:|
| Watch read | 1.165 ms | 0.665 ms |
| Connections per watch read | 2 | 1 |
| Base `import ternela` | 32.853 ms | 0.164 ms |
| SDK session response in clean install | 37,233 bytes | 55 bytes |

Watch reads improved about 43% in this local experiment; this is not a production
latency guarantee. Cold creation, submissions and outbox timings varied, and warm
creation was slightly slower in this sample. No across-the-board speedup or reduced
provider latency is claimed. Lazy import defers HTTP import work; actually using the
SDK still loads it. The compact response avoids building the UI catalog entirely.

Compressed raw benchmark, artifact probes, hashes, logs and the test-state recovery
backup live in ignored `data/audits/python-package/`. They are excluded from both
Python distributions and the application bundle.

## Remaining organization work

The compiler still uses existing `*_lab` packages; source/development tools still
require a checkout. Move that compiler into a product namespace in a separately
measured migration with compatibility aliases. Preserve the reviewed connector,
shared evaluator, repository and schedule ownership boundaries. This pass does not
add authenticated connectors, model inference to the demo, distributed workers,
PyPI publication, native Windows support, or a new update mechanism.

[Install and build contract](../python-package.md).
