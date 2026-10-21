# Local distribution contract, frozen before implementation

One Docker container runs on macOS, Windows (Linux containers) or Linux. Docker
Desktop/Engine is the only host runtime dependency. The scripts create one managed
folder, publish only a loopback port, and never mount the Docker socket. Docker
itself owns its image/container storage outside that folder.

Automatic updates default to off. Installation asks once, defaulting to no;
noninteractive installation requires an explicit choice. Users can change that
choice later. Checks run while the container is running, not while the computer or
Docker is stopped. Manual update is a separate, explicit action.

The existing GitHub observation runtime records release evidence before an updater
considers installation. Only a newer stable `vMAJOR.MINOR.PATCH` published release
from the bundled repository may update the app. The archive must have the exact
expected asset name, uploaded state, bounded size, GitHub-provided SHA-256 digest,
matching internal version/repository/runtime manifest, and matching file hashes.
This trusts repository publishers and GitHub/TLS; a checksum is not an independent
publisher signature. An incompatible Python/runtime version requires a new image.

Failure cases to establish before implementation:

1. Default-off and explicitly disabled installations make no scheduled update read.
2. Missing release/404, rate limit, offline, timeout and invalid JSON leave the app running.
3. Draft, prerelease, missing publication date, malformed version, same version and
   downgrade do not install.
4. Missing/duplicate asset, wrong download URL, missing/wrong digest, truncated or
   oversized download do not stop the current service.
5. Archive traversal, absolute paths, links, duplicate members, unexpected files,
   excess expanded size, wrong file hashes, wrong repo/version/runtime and missing
   entrypoint fail before execution.
6. Update download and validation happen while the current app is running. A data
   backup happens only after stopping it and before candidate code can write data.
7. Candidate startup failure restores the previous app and its pre-update data.
8. Interrupted update is recovered on supervisor restart; a committed update is
   preserved. A rollback failure stops rather than starting with uncertain data.
9. Failed versions are not retried automatically in a loop. Manual update may retry.
10. Restart preserves watches and update consent. Logs, retained app copies and
    pre-update backups have explicit retention bounds.
11. Concurrent supervisors cannot share state. A manual request is durable and
    completed only after success/failure is recorded; consent is rechecked before apply.
12. Removal deletes only the managed container by default and retains all data.
    A separate, explicit purge deletes the marked install folder; foreign folders,
    symlinks and conflicting container names are refused.
13. Port conflicts and a stopped/missing Docker engine produce actionable errors.
14. Host/Origin/token protections remain enforced with the container's internal bind.

Release artifacts are built locally; this work does not publish a release, push
code, create CI or promise native executables. Host scripts are platform entry
points for the same Docker-backed application.
