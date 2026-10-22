# Use Ternela as a Python package

The wheel includes the local HTTP service, UI, reference agent demo and verified,
compressed public source evidence. It has **zero third-party runtime Python
dependencies**. macOS with Python 3.11+ is the supported host; Windows is not supported.
The Docker installer remains available for people who do not want host Python.

Download `ternela-0.1.3-py3-none-any.whl` and `SHA256SUMS` from the
[v0.1.3 GitHub release](https://github.com/Alphabetsoup16/ternela/releases/tag/v0.1.3).
Verify the wheel's SHA-256 against its line in that file. This release is distributed
on GitHub; `pip install ternela` from PyPI is not an installation instruction.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m ternela.setup_demo
python -m pip install '.[dev]'
python -m build
python scripts/test-wheel.py dist/ternela-0.1.3-py3-none-any.whl
```

Build preparation verifies pinned source hashes. A missing source downloads during
preparation; an existing corrupt source fails. Both the source distribution and
wheel carry only the reviewed public evidence allowlist. Building a wheel from the
source distribution requires build tools, but requires no further provider downloads.
Build dependencies may still need a package index unless already installed.

Install the resulting wheel in a separate environment:

```sh
python3 -m venv ~/ternela-python
~/ternela-python/bin/python -m pip install /absolute/path/to/ternela-0.1.3-py3-none-any.whl
~/ternela-python/bin/ternela --data ~/ternela-data
# Open http://127.0.0.1:8765, then choose Run agent demo.
```

`python -m ternela` and `ternela` start the same service. `--port` chooses another
loopback port. Stop with Ctrl-C. The command runs in the foreground; installation
adds no daemon and does not opt into automatic updates.

## One data location

`ternela.paths` separates immutable package resources from writable local state:

| Location | Contents |
|---|---|
| Installed package | Python, UI assets, compressed public evidence and its provenance |
| `--data PATH` / `TERNELA_DATA` | SQLite, receipts, watches, compiler runs and local source overrides |
| Default installed data on macOS | `~/Library/Application Support/ternela/data` |
| Default source checkout data | `data/` in the checkout |

`--data` takes precedence over the environment and is inherited by subprocesses.
For a library process, set `TERNELA_DATA` **before importing application modules**.
The existing `Store(root)` and `Watches(path, store)` remain available for explicit
in-process storage. The service and updater derive their writable paths from that
store; package resources are never a write destination. Missing local evidence
falls back to bundled evidence; a present corrupt override fails verification.

Upgrading the wheel replaces installed code and public resources, preserving the
separate data folder. Stop the service first. Removing the package does not remove
that folder. Managed Docker installations continue to use their existing volume and
update/rollback mechanism; a wheel is not an automatic application-update bundle.

## Python client

Start the service, then connect from another process:

```python
from ternela import Client

client = Client("http://127.0.0.1:8765")
watch = client.release("pypi", "httpx", "0.28.1")
print(watch["id"])
```

This creates a watch. Collection, schedules and delivery are explicit operations;
creating a watch alone does not guarantee that the package exists or start polling.
See the [runtime contract](product-runtime.md) and [monitoring example](monitoring.md).

## Build and test boundaries

The wheel probe installs with `--no-index --no-deps` into a temporary environment,
runs outside the checkout, makes installed source files read-only, and tests source
verification, deterministic replay, a full saved-task compiler run using a local CLI stand-in, HTTP assets/bootstrap,
state isolation and the actual reference-agent handoff. It stops only its own server
process group. It invokes no model and contacts no provider. Existing unit tests cover
provider faults, expiry, database migration, recovery and delivery semantics.

The active compiler and its prompts live in `ternela.compiler`. It uses core
schemas, source loading and admission without importing `workbench` or `*_lab`.
Legacy adapters remain packaged for compatibility; historical evaluators and private
research fixtures are not compiler inputs. The offline test CLI exercises the real
subprocess/file protocol and admission, not a real model or credentials. Development,
rebranding and release-maintenance commands require the full source checkout.
