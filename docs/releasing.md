# Prepare a local application release

The application bundle is the update unit. A GitHub source archive alone cannot
trigger automatic execution. There is no publishing workflow or CI added here.

1. Review and commit the intended source changes. Update both `pyproject.toml` and
   `ternela/__init__.py` to the same stable version. Keep the technical namespace
   `ternela` stable; the release repository comes from `ternela/brand.json`.
2. Run `python3 -m ternela.setup_demo`, `python3 -m ternela.check`, and
   `python3 -m ternela.regression`. Build and smoke-test the Docker image, including
   a fresh folder and an existing-state upgrade.
   `python3 -m examples.distribution_probe --image YOUR_LOCAL_IMAGE` derives its
   synthetic upgrade versions from the checkout and verifies real rollback,
   failed-image fallback and commit-interruption recovery. The image must match
   the checkout's version and audited launcher code.
3. Run `python3 -m ternela.distribution build`. It writes
   `dist/ternela-vVERSION.tar.gz` and `dist/SHA256SUMS`. It verifies the produced
   archive against its manifest. Repeating the build with identical inputs yields
   identical bytes. Freshly fetched supplemental docs carry real fetch timestamps
   and can change those inputs; this is not a claim of reproducible network builds.
4. Commit the exact tested source, tag it `vVERSION`, and create a **draft** GitHub
   release on the configured repository. Upload the **exact built bundle and
   SHA256SUMS**. Verify asset names, sizes and GitHub's SHA-256 metadata against
   local bytes; download the draft assets and compare their hashes. Confirm the tag
   resolves to the tested commit. Only then publish the draft as a stable release.
   Only macOS hosts are supported for the first release. Linux containers remain
   the runtime; Windows and Linux host support require separate qualification.
5. Check from an opted-in disposable installation. Confirm the recorded release
   watch, installed version, surviving watches and retained backup. Until a real
   release is published, local/mocked upgrade tests do not establish this last hop.

Bundle format 1 supports application updates on Python 3.15 and launcher runtime 1.
Changing that contract requires a new image and explicit compatibility/recovery
testing. The immutable verifier also defines the allowed top-level bundle files;
test new bundles against the installed launcher, not only newly built images.
The base image is pinned by a multi-platform digest; review and rebuild it
for runtime/security updates. Automatic app updates do not patch base-image packages.

The builder uses an explicit source-directory/file-extension allowlist. Seeds
include pinned compressed API specs, pagination guides, and only the attributed
Slack supplement blobs—not active watches, credentials, arbitrary local blobs,
Git metadata or generated run data. Inspect the bundle before publishing. Apache
2.0 and upstream notices remain included.

For a manual runtime rebuild, use the new reviewed checkout or extracted bundle:

```sh
# Preserve the existing install folder and native state volume. Replace ID with its .ternela-install value.
sh ~/ternela/ternela.sh stop
docker build -t ternela-local:ID .
sh ~/ternela/ternela.sh start
```

`start` uses Compose's image-change detection to recreate the container. Use the
equivalent PowerShell stop/start commands on Windows. Host scripts/configuration
are intentionally not rewritten by app updates; review and copy changes from the
new release when a runtime migration requires them.

The installer pins the Docker engine identity. Preserve that marker with the
folder; changing Docker contexts must not turn removal into a local-data deletion
while its original container is still running. The PowerShell filesystem/control
probe is `powershell -NoProfile -File scripts/test-powershell.ps1`; it mocks Docker
and does not replace testing Docker Desktop on Windows.
