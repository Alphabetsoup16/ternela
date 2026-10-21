# Local Docker storage

The user approved native Docker storage on 2026-10-20. The local demo has been
migrated and verified. See the [storage audit](audits/2026-10-20-storage.md).

The install folder contains management scripts, engine/install ownership markers,
Compose configuration and compressed exported backups. Live state uses the named
Docker volume `ternela-INSTALL_ID-state`, labeled with its installation ID. Both
remain on this computer. The runtime is unprivileged and published on loopback only.

This avoids the macOS shared folder whose advisory locks failed our independent
competing-process probe. The actual native volume passed `flock`, `lockf`, SQLite
writer exclusion and the full test suite. This is evidence for this configuration,
not a claim about every Docker sharing backend or filesystem.

Migration stopped the old app, compressed the original installation and verified
all 245 file hashes and 310 file/directory/link entries after copying. SQLite
integrity, schema history and all database rows matched, including 13 watches,
4 consumer checkpoints and 12 queued messages. Update consent stayed off. The
original application is recoverable from the pre-migration snapshot and retained
image; the live demo uses the verified current source.

The launchers provide `backup` and `restore`. Both acquire the same native
supervisor lock as the app. Backups are full compressed stopped-state snapshots;
restore verifies its manifest and file hashes before replacing state. A journal
allows interrupted replacement to roll back on restart. A restore first exports
the current state and resumes an app that was previously running. A restart failure
is reported even if creating the backup succeeded.

Normal removal retains the volume and folder. Purge checks engine, installation,
container-project and volume-label ownership before deleting managed resources.
The scripts refuse unmigrated folder-backed installations. Copying the install
folder alone does not include live state: export a backup for portability.

See [local installation](local-install.md) for commands and transfer limits.
Native Windows execution is still unverified; no cloud storage or public release
was introduced.
