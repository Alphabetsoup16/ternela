# Local distribution audit — 2026-10-19

Added one Docker-backed distribution with POSIX and PowerShell management scripts.
The scripts install to one dedicated folder, ask for update consent (default no),
and provide start, stop, status, logs, manual update, consent changes and removal.
Normal removal retains the folder/image; an explicit purge removes them. Docker
owns its own image/container/cache storage outside the application folder.

## Evidence and scope

- **223 unit/API tests pass** on Python 3.15: 128 general, 9 safety, 86 workbench.
- **41/41 existing simulated regression scenarios pass**, with zero structural
  issues or false ready/complete outcomes in that set.
- A clean ARM64 Docker build fetched/verified pinned source specs and prepared
  attributed supplements, without carrying the research store into the image.
- The real Mac installer succeeded using a path containing spaces and port 8768.
  A live GitHub Status watch and its receipt survived a stop/start. Update consent
  also survived restart, and a command queued while stopped ran after restart.
- A real manual check recorded a GitHub watch with unknown release evidence. It
  did not install anything. The final interface reports this as `unavailable`,
  distinguishing uncertainty from a known absence of an update.
- A disposable container ran actual 0.1.0 → synthetic 0.1.1 process startup, then
  a deliberately broken 0.1.2 entrypoint that modified data and exited. The old app,
  its watch and its data were restored. A simulated uncommitted transaction was
  recovered on a subsequent supervisor initialization. Download metadata was not
  a real published release in these induced upgrade tests.
  The automatic-check entry point ran the real bounded download verifier, bundle
  extraction and installer with injected release evidence/remote response bytes;
  a failed version was suppressed on its next automatic check.
- PowerShell parsed and its install/control/removal flow passed with mocked Docker
  in Microsoft's official PowerShell 7 Linux image. **Windows PowerShell 5.1 and
  Windows Docker Desktop were not available for native platform validation.**
  The script uses 5.1-compatible syntax; that is not a substitute for Windows QA.
- The pinned official Python image index includes linux/arm64 and linux/amd64.
  The application image was executed on ARM64; AMD64 availability is not a claim
  that this application was run on every architecture.
- The measured runtime image was about 52 MB; the compressed application bundle
  about 2 MB. These are local build observations, not registry transfer guarantees.

The [failure contract](../experiments/local-install-v1.md), its freeze record and
six initial distribution test functions preceded implementation. The initial run
failed because the distribution module did not exist. The original test file was
subsequently formatted; its release/corpus expectations were not relabeled. Further
transport, lifecycle, receipt and custom-port tests were added during implementation.

## Defects found and corrected

The first clean image build copied source-cache metadata without its referenced
supplement blobs. Replaced broad inclusion with a source-only build context; new
images prepare their own verified sources. `/state` is excluded from Docker and Git.
The cancellation mock initially created a directory before the installer removed
stale candidates; it now models extraction creating the directory. Harness review
also moved candidate staging after pruning, so the broken entrypoint is actually
executed. Frozen release expectations were unchanged.

Review caught two additional transaction hazards: an exited service leader could
leave compiler descendants alive during backup, and a damaged backup could be
restored without a whole-file integrity record. The supervisor now fences the
process group and checks the backup's SHA-256/size before changing data. Corrupt or
uncertain rollback stops and retains its recovery journal rather than continuing.

The full automatic-entry-point test first used an older disposable image whose
immutable allowlist predated `.gitignore` in the bundle. It safely rejected that
archive before execution. The final image accepted it and passed the full pipeline.
This is a launcher compatibility boundary, and release preparation now calls it out.

## Update boundary

Automatic checks use the existing GitHub collector, typed checks, persisted watches
and receipts. A separate deterministic installer requires a newer stable version,
exact repository/asset URL, uploaded state, bounded size, GitHub SHA-256 digest,
valid internal file manifest and compatible launcher/Python contract. It rechecks
consent and receipt freshness before applying. Invalid evidence keeps the current
service running. Failed versions are not repeatedly applied automatically.

Updates change the application bundle. Host scripts, the immutable supervisor,
Python and OS packages require an explicit image rebuild. A checksum is not an
independent publisher signature. Startup recovery does not establish correctness
of all future application behavior, power-loss durability or production reliability.

No code was pushed, registry image uploaded, GitHub release published or CI created.
The live published-release → verified asset → automatic upgrade path still needs
a real release trial before inviting users to rely on automatic updates.

[Install guide](../local-install.md) · [Release preparation](../releasing.md).
