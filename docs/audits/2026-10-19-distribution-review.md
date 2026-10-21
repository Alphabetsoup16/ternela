# Distribution review — 2026-10-19

Reviewed the local distribution added in the preceding pass: installer ownership,
Docker selection/configuration, archive admission, update transactions, backup and
recovery, command delivery, release preparation and the accuracy of its documentation.
This review found real defects despite the previous 223-test suite passing.

## Findings corrected

| Area | Reproduced failure | Result after repair |
|---|---|---|
| Rebuilt image startup | A failed newer bundled app was reapplied on each restart, including after recovering an interrupted attempt. | Keep the restored app serving and remember the rejected version. |
| Commit publication | A directory-fsync error after replacing `current.json` restored old data while the marker already named the new app. | Retain the journal and candidate data; restart resolves the visible commit. |
| Recovery identity | A stale journal for different versions could restore an unrelated backup over current data. | Validate journal/current identity before any restore. |
| Consent | Losing `settings.json` on an existing install could re-enable the original environment opt-in. | Default missing/ambiguous consent to off for an existing install. |
| Backup contents | Tar encoded hard-linked regular files as link entries, but restore refuses links. | Snapshot each hard link's bytes as an ordinary file. |
| Command recovery | Invalid JSON poisoned the queue; a crash after saving a result could replay its completed request. | Complete invalid commands with an error and skip requests that already have results. |
| Docker selection | Selecting a different engine could remove no container and still purge local files. | Record/verify engine identity before management and removal. |
| Configuration | Shell `TERNELA_*` variables could override saved image/port/user/consent values. | Isolate Compose interpolation; PowerShell restores the caller's environment. |
| Folder ownership | A trailing separator hid an install-folder symlink from the POSIX guard. | Normalize separators before checking links and recheck canonical protected roots. |
| Update retries | A network `OSError` after selecting a target permanently suppressed that version. | Retry transient I/O failures at a later check; retain suppression for rejected bundles/candidates. |
| Bundle data | A validly hashed seed could name a watch database rather than source material. | Accept only source-document/source-blob paths in the seed. |
| Strict metadata | Unicode decimal digits and bool/float runtime identifiers were admitted as stable/integer values. | Require ASCII stable versions, actual integer runtime fields and unambiguous local JSON; malformed rejection records block automatic application. |

These are reliability and boundary fixes, not claims of independent publisher
authentication. A trusted publisher still controls executable application code.

The integration helper also hardcoded the initial version/repository. It now derives
its fixtures from the checkout, so version bumps and rebranding do not silently
invalidate the next release's recovery test.

## Validation

- **241 unit/API tests pass**: 146 general, 9 safety, 86 workbench. The original
  223 tests still pass. The first 16 added tests were also run against the unchanged
  baseline: 15 failed and the existing foreign-folder guard already passed.
  Some baseline failures identify missing recovery handlers rather than distinct bugs;
  this is not a count of 15 independent vulnerabilities. Two further tests reproduced
  duplicate consent keys and malformed failed-version metadata before their repair.
- **41/41 known simulated regression scenarios pass**, zero structural issues and
  zero false ready/complete outcomes in that set.
- A fresh ARM64 image runs actual candidate processes through the automatic-check,
  verified-byte download, extraction, upgrade, bad-startup rollback and restart paths.
  Injected remote responses avoid publishing a test release. Watch receipts survive.
- The Docker probe additionally checks failed bundled-image startup across repeated
  initialization and injects an error immediately after publishing the commit marker.
  Recovery preserves the committed version/data and the original watch.
- A real Mac install with conflicting parent environment variables still selected
  port 8770 and automatic updates off. Its engine marker, live GitHub Status receipt
  and preferences survived stop/start.
- The PowerShell filesystem/control probe passes in Microsoft's official PowerShell
  7 Linux image with mocked Docker: installation, saved settings, caller environment
  restoration, wrong-engine refusal, normal removal and purge. Its container has no
  network, Docker socket or application checkout: only lifecycle scripts, generic
  Compose configuration and an inert Dockerfile are mounted.
- Ruff lint/format, POSIX shell syntax and JavaScript syntax pass. No frontend
  behavior or design was changed. Source inventories and compressed logs accompany
  the machine-readable report in ignored `data/audits/distribution-review/`.

The full-checkout PowerShell mount was rejected by automatic approval review for
source exposure. The minimized fixture above completed the verification safely.
The rejection was not bypassed by mounting the checkout indirectly.

## Remaining release gates

Native Windows PowerShell 5.1/Docker Desktop and a real published-release download
have **not** been validated. Linux PowerShell/mocked Docker does not establish native
Windows filesystem, quoting, ACL or mount behavior. The application image was run on
ARM64; this review does not claim execution on AMD64. Process-interruption recovery
is not a power-loss, filesystem-corruption, zero-downtime or production-SLA proof.

The reviewed code remains local. No release was published, no registry image was
uploaded, no CI was added, and no account/provider credentials or paid model were used.
Existing pre-publication installs require explicit engine-marker verification before
adopting the new host scripts. Keep prior backups and publishers trusted.

[Install contract](../local-install.md) · [Release preparation](../releasing.md) ·
[Machine-readable record](2026-10-19-distribution-review.json).
