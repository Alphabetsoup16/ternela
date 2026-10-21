# Colima on macOS

Ternela works with Colima's Docker runtime and native named volumes. The managed
launcher records a Docker context and engine identity; it does not need to change
the global context or connect to the Docker socket from inside the container.

Use an existing Colima profile, or install the standalone tools:

```sh
brew install colima docker docker-compose docker-buildx docker-credential-helper
```

Ensure Compose and Buildx are discoverable as Docker CLI plugins using the
[Colima installation instructions](https://colima.run/docs/installation/).
If replacing Docker Desktop, check that the CLI, plugins and registry credential
helper remain installed independently of Docker.app. Migrate existing credentials
without displaying or writing their secret values to reports.

For a fresh Apple Silicon profile, 2 CPUs and 4 GiB RAM were sufficient for our
Ternela tests; shared workloads may need more. Inspect existing profile settings
before changing them. Starting or restarting the VM affects every Colima project.

```sh
# For a fresh profile only; preserve settings when one already exists.
colima start --activate=false --runtime docker --vm-type vz --cpus 2 --memory 4
DOCKER_CONTEXT=colima sh scripts/ternela.sh install
```

For an existing profile, `colima start --activate=false` uses its saved settings.
`--activate=false` leaves other agents' global Docker context intact. The managed
install's `.ternela-context` selects Colima for status, lifecycle, updates and binary
backup/restore transfers, even when a shell's default context still points at Desktop.

## Moving an existing install

Desktop and Colima are different engines: their volumes and image stores are
separate. Switching the client context does not copy state. Keep a working Desktop
installation available until the selected project's migration is verified.

1. Check engine/install identity and volume ownership. Capture its configuration,
   current version, consent, database rows and consumer checkpoints.
2. Transfer or rebuild the required image. Stop the source writer, then export a
   consistent compressed state backup with its manifest. Keep the source volume.
3. Restore into an owned empty native volume on Colima. Verify file hashes, links,
   SQLite integrity, schema history and all table rows before starting it.
4. Only then update the install's engine/context markers. Preserve its install ID,
   loopback port and consent. Start with its managed launcher.
5. Check live behavior, locks, restart/delivery recovery and backup/restore. On a
   failure, stop the destination before restoring the original management markers
   and restarting the preserved source. Do not run both copies as active services.

Do not edit the engine marker merely to bypass an ownership error. Neither
`docker export` nor an image archive includes mounted database data. The migration
operator must cover all named/anonymous volumes and bind mounts in the project.
Our local Ternela migration used a stopped full-state export and exact comparison.

Host paths may need Colima mounts and macOS privacy permissions. Ternela's live
volume and streamed backup commands avoid depending on a Documents folder bind
mount. Keep SQLite and supervisor locks on native Linux storage; verify their
behavior if changing storage backends.

The [migration audit](audits/2026-10-20-colima.md) records this Mac's validation.
Other projects' migrations, Docker Desktop removal, native Windows operation,
power-loss durability and performance under production load are outside that result.
