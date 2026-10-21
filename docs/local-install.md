# Run locally with portable backups

**Supported host: macOS.** Use [Docker Desktop](https://docs.docker.com/desktop/)
or [Colima](https://colima.run/docs/installation/) with the Docker runtime. Start
your chosen engine before installing. The application runs inside a Linux container.
Windows and Linux hosts are unsupported for this first alpha release. PowerShell
and Linux instructions below are retained as development reference.
No host Python, provider account, administrator install or PATH change is needed.

There is no prebuilt registry image yet; the installer builds locally from the
checkout or application release you supply.

## Install

From a checkout or extracted application release:

**macOS**

```sh
sh scripts/ternela.sh install
```

**Windows PowerShell (unsupported development reference)**

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File .\scripts\ternela.ps1 install
```

Both ask: **automatically install newer stable releases while running? [y/N]**
Pressing Enter chooses no. A noninteractive install must pass `--auto-update` or
`--no-auto-update` (PowerShell: `-AutoUpdate` or `-NoAutoUpdate`). This consent permits
future publisher code to run inside the container with access to Ternela's data
and network. Only enable it for a repository you trust.

The first build downloads the pinned Python image, Debian curl packages and public
API specifications. It produces a local image, starts one container and waits for
health before printing **http://127.0.0.1:8765**. If the port is busy, choose
`--port 8766` / `-Port 8766`. Choose another folder with `--dir` / `-Dir`.
The installer refuses to overwrite an existing folder.
It records the Docker engine identity and refuses management/removal against a
different engine. Switch back to the original Docker context if that check fails.
New installs record the selected Docker context in `.ternela-context`, alongside
its engine identity. Management and binary backup/restore commands select that
context explicitly; changing the global context or inherited `DOCKER_HOST` does
not redirect them. Older installs without that marker retain their original
current-context behavior and engine check. A migration must back up and verify
the destination before changing the engine/context markers.
Saved install settings take precedence over inherited `TERNELA_*` shell variables.

For an existing Colima setup, install into its context without changing other
projects' default:

```sh
colima start --activate=false
DOCKER_CONTEXT=colima sh scripts/ternela.sh install
```

Only macOS is supported. The retained PowerShell path has not been validated on Windows.

An extracted archive may carry Windows' download mark. Review and unblock the
script if RemoteSigned rejects it; the command does not change machine-wide policy.

## Manage

The installer copies both management scripts into `~/ternela` (`$HOME\ternela` on
Windows). They find that folder themselves when run from there.
After installation, the original checkout or extracted download can be removed;
normal starts and application updates use the managed folder and local image.

| Action | macOS | PowerShell reference (unsupported) |
|---|---|---|
| Show version and consent | `sh ~/ternela/ternela.sh status` | `& "$HOME\ternela\ternela.ps1" status` |
| Check and install now | `sh ~/ternela/ternela.sh update` | `& "$HOME\ternela\ternela.ps1" update` |
| Enable automatic updates | `sh ~/ternela/ternela.sh updates on` | `& "$HOME\ternela\ternela.ps1" updates on` |
| Disable automatic updates | `sh ~/ternela/ternela.sh updates off` | `& "$HOME\ternela\ternela.ps1" updates off` |
| Save a compressed backup | `sh ~/ternela/ternela.sh backup` | `& "$HOME\ternela\ternela.ps1" backup` |
| Restore a backup | `sh ~/ternela/ternela.sh restore --file PATH` | `& "$HOME\ternela\ternela.ps1" restore -File PATH` |
| Stop / start | `sh ~/ternela/ternela.sh stop` / `start` | `& "$HOME\ternela\ternela.ps1" stop` / `start` |
| Read logs | `sh ~/ternela/ternela.sh logs` | `& "$HOME\ternela\ternela.ps1" logs` |
| Remove container, retain folder/image | `sh ~/ternela/ternela.sh remove` | `& "$HOME\ternela\ternela.ps1" remove` |
| Delete container, live volume, folder and local image | `sh ~/ternela/ternela.sh remove --purge` | `& "$HOME\ternela\ternela.ps1" remove -Purge` |

`--purge` / `-Purge` deletes the owned live volume, watches, receipts and folder backups. Export or
copy anything you need first. Neither removal command uninstalls Docker or prunes
shared image/build caches. A normal `remove` can be reversed with `start`.

The container restarts with Docker unless explicitly stopped. Keep the chosen
container engine running; this installer does not change login-start preferences or add an OS
scheduled task. Closing the browser does not stop monitoring.

## Files and retention

The install folder contains both launchers, `compose.yaml`, `docker.env`,
`.ternela-install`, `.ternela-engine`, `.ternela-context`, `.ternela-storage` and `backups/`.
Live state resides in the local Docker volume `ternela-INSTALL_ID-state`, labeled
with that installation's ID. The scripts refuse a volume belonging to another install.
The container uses a fixed unprivileged UID/GID 1000; host user IDs are unnecessary.

A migrated legacy folder may retain its old `state/` as an inactive rollback copy;
the running container uses the volume.

The volume contains persisted consent, current/previous extracted applications,
SQLite watches, compressed evidence, an upgrade recovery journal and backup,
rotating updater logs and the last ten command results. Evidence is retained;
its size depends on your watches. Docker also stores the image and bounded logs.

**Why a volume:** the macOS shared folder used in our test did not enforce
cross-process advisory locks. Native Docker storage passed those tests. Keep live
SQLite and supervisor locks on native storage; copying the install folder alone
does not copy the live database. Everything remains on your computer.

`backup` briefly stops the app, acquires its supervisor lock and writes a full
compressed state snapshot into `backups/`. It restarts the app if it was running.
The archive includes a versioned manifest and SHA-256 checks of every regular file.
Keep an off-device copy if you need protection against losing this computer.

`restore --file PATH` / `restore -File PATH` first saves the existing state, validates
the archive completely, then replaces state. An interrupted replacement rolls back
on the next start. Paths, special files and unexpected links are rejected. Transfers
currently accept at most 100,000 entries and 8 GiB of uncompressed content. Backups
contain private evidence and local tokens; keep them private. Hashes detect changed
bytes; they do not authenticate whoever supplied an archive. Restore only trusted
snapshots. An archive includes application code and the saved update preference.
Restore needs temporary space for another uncompressed state copy as well as the
retained compressed backup. These checks do not establish power-loss durability.

For another computer, install from a compatible checkout/release, then restore a
backup into that initialized install. Engine identity markers are local management
metadata; do not transplant them to bypass engine ownership checks. Runtime/Python
compatibility still applies. Native Windows execution requires separate validation.

Normal `remove` preserves the live volume and folder; `start` reconnects them.
Purge removes only the volume carrying this install's ownership label. No Docker
socket, host home directory or credentials are mounted.

## What updates actually do

While enabled, the supervisor checks after startup, then about hourly with jitter.
It runs an ordinary Ternela watch against the bundled repository's latest published
GitHub release. The watch checks draft/prerelease/publication fields and a tag
different from the installed version, saving a receipt in the same evidence store.
`status` reports that watch ID; open `/?watch=ID` to inspect its evidence.

A separate installer gate requires a strictly newer stable `vMAJOR.MINOR.PATCH`,
exact uploaded asset name, bounded download size and GitHub's SHA-256 asset digest.
It validates every extracted file, repository, version and runtime manifest before
stopping the old service, and rechecks the receipt's freshness before applying it.
No release, bad evidence, rate limiting, unavailable
network, corrupt downloads and incompatible bundles leave the current app running.
This polls GitHub; it does not receive push notifications or need a webhook endpoint.

After validation, it stops the app, compresses a full data backup, starts the
candidate and checks its version, sources and workbench bootstrap. A failed startup
verifies the backup's size/hash and restores the old data and app. An interrupted
uncommitted upgrade is recovered on restart. Invalid bundles and failed candidate
versions require a manual retry or a different release; transient network errors
can retry at the next scheduled check. A broken app bundled in a rebuilt image
keeps the restored app running and is not retried on each restart. Update
consent is checked again immediately before applying an automatic update.
If an existing install loses its consent file, automatic updates default to off.
An error immediately after publishing the version marker retains its recovery
journal; restart resolves the visible commit before choosing which app to run.

Automatic upgrades change the application bundle, not the image's Python, curl,
immutable supervisor or installed host scripts. Runtime-format/Python changes
require rebuilding the image from a new checkout/release and recreating the
container with the same native state volume. The new image applies a newer bundled app
through the same backup/startup checks. Review release notes for such migrations;
future supervisors must preserve the recovery contract before claiming compatibility.

SHA-256 detects mismatched bytes. Trust comes from your chosen repository,
GitHub's HTTPS/asset metadata and its publishers; this is not an independent
publisher-signature system. Startup rollback is not a guarantee against a later
application bug. Keep your own backups for valuable data.

## Docker directly

The scripts above are the supported cross-platform install path. For a source
checkout on macOS, Compose can also run directly:

```sh
docker compose up --build --detach --wait
# http://127.0.0.1:8765
docker compose down
```

Direct Compose uses a native `ternela-dev-state` volume by default. Set a distinct
`TERNELA_INSTALL_ID` when running multiple checkouts. `docker compose down` retains
the volume. Management-script removal applies only to marked installations.
The container runs without root/capabilities on a read-only image filesystem, with
native `/state` and bounded `/tmp` writable. Host/Origin/session-token checks apply.

Bundled replay and public connectors work in this image. Arbitrary task compilation
requires Codex CLI and its login; those are not installed or mounted into the image.

[Colima operations](colima.md) · [Release preparation](releasing.md) · [Failure contract](experiments/local-install-v1.md)

Pre-publication installs that lack `.ternela-engine` must keep their original
management scripts or be migrated after verifying the engine and container mount.
The new scripts do not silently adopt an unverified legacy folder. Back up data
before reinstalling or migrating. This check does not relocate data between engines.

Folder-backed legacy installations require a stopped, backed-up and hash-verified
migration before using the native-storage scripts. The `.ternela-storage` marker
records this boundary; new scripts refuse to silently adopt an unmigrated folder.
