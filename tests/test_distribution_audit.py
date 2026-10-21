# SPDX-License-Identifier: Apache-2.0
"""Faults reproduced during the post-implementation distribution audit."""

import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_distribution import REPO, archive
from test_local_service import FakeService

from ternela.distribution import extract_bundle, version
from ternela.local_service import read, save


class DistributionAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = FakeService(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def bootstrap(self):
        self.service.bootstrap = archive(self.root)
        self.service.repository = REPO

    def test_failed_bundled_upgrade_stays_on_old_version_across_restarts(self):
        self.bootstrap()
        self.service.fail_target = "0.2.0"
        self.service.initialize()
        self.assertEqual(self.service.current()["version"], "0.1.0")
        self.assertEqual(read(self.root / "failed-version.json"), {"version": "0.2.0"})
        first = list(self.service.started)
        self.service.initialize()
        self.assertEqual(self.service.started, first)
        self.assertEqual((self.root / "data/sentinel").read_text(), "original")

    def test_recovered_bundled_upgrade_does_not_retry_the_failed_candidate(self):
        self.bootstrap()
        self.service.backup()
        save(self.root / "transaction.json", {"old": "0.1.0", "target": "0.2.0"})
        (self.root / "data/sentinel").write_text("interrupted candidate")
        self.service.initialize()
        self.assertEqual(self.service.started, [])
        self.assertEqual((self.root / "data/sentinel").read_text(), "original")

    def test_error_after_publishing_commit_does_not_restore_old_data(self):
        def uncertain_save(path, value):
            save(path, value)
            if Path(path).name == "current.json":
                raise OSError("directory fsync failed after replace")

        with patch("ternela.local_service.save", side_effect=uncertain_save):
            with self.assertRaises(OSError):
                self.service.apply("0.2.0")
        self.assertEqual(self.service.started, ["0.2.0"])
        self.assertTrue((self.root / "transaction.json").exists())
        self.assertEqual((self.root / "data/sentinel").read_text(), "candidate changed data")
        self.service.recover()
        self.assertEqual(self.service.current()["version"], "0.2.0")

    def test_missing_consent_on_an_existing_install_defaults_off(self):
        self.bootstrap()
        save(self.root / "current.json", {"version": "0.2.0", "previous": "0.1.0"})
        (self.root / "settings.json").unlink()
        with patch.dict(os.environ, {"TERNELA_AUTO_UPDATE": "true"}):
            self.service.initialize()
        self.assertFalse(self.service.consent())

    def test_generated_backup_of_hardlinked_regular_files_is_restorable(self):
        os.link(self.root / "data/sentinel", self.root / "data/duplicate")
        self.service.backup()
        (self.root / "data/sentinel").write_text("changed")
        self.service.restore()
        self.assertEqual((self.root / "data/duplicate").read_text(), "original")

    def test_malformed_command_does_not_poison_the_queue(self):
        (self.root / "commands").mkdir()
        (self.root / "commands/bad.json").write_text("invalid JSON")
        save(self.root / "commands/good.json", {"action": "update"})
        with patch.object(self.service, "check", return_value={"status": "no_update"}) as check:
            self.service.process_commands()
        self.assertEqual(check.call_count, 1)
        self.assertEqual(read(self.root / "results/bad.json")["status"], "error")
        self.assertEqual(list((self.root / "commands").iterdir()), [])

    def test_completed_command_is_not_replayed_after_interruption(self):
        save(self.root / "commands/done.json", {"action": "update"})
        save(self.root / "results/done.json", {"status": "updated", "version": "0.2.0"})
        with patch.object(self.service, "check") as check:
            self.service.process_commands()
        check.assert_not_called()
        self.assertEqual(read(self.root / "results/done.json")["status"], "updated")

    def test_unrelated_recovery_journal_cannot_replace_current_data(self):
        self.service.backup()
        save(self.root / "current.json", {"version": "0.3.0", "previous": "0.2.0"})
        save(self.root / "transaction.json", {"old": "0.1.0", "target": "0.2.0"})
        (self.root / "data/sentinel").write_text("current v0.3 data")
        with self.assertRaises(ValueError):
            self.service.recover()
        self.assertEqual((self.root / "data/sentinel").read_text(), "current v0.3 data")

    def test_transient_download_failure_can_retry_automatically(self):
        from test_distribution import metadata

        save(self.root / "settings.json", {"automatic_updates": True})
        evidence = {"decision": "complete", "release": metadata(), "expires_at": time.time() + 300}
        with (
            patch.object(self.service, "probe", return_value=evidence),
            patch(
                "ternela.local_service.download_asset", side_effect=OSError("network unavailable")
            ) as download,
        ):
            self.service.check(automatic=True)
            self.service.check(automatic=True)
        self.assertEqual(download.call_count, 2)
        self.assertFalse((self.root / "failed-version.json").exists())

    def test_stable_version_does_not_accept_unicode_decimal_digits(self):
        with self.assertRaises(ValueError):
            version("1\u0662.0.0")

    def test_manifest_runtime_fields_require_integer_types(self):
        for change in ({"format": True}, {"runtime": 1.0}, {"python": [3.0, 15]}):
            path = archive(self.root, change=lambda manifest: manifest.update(change))
            with self.assertRaises(ValueError):
                extract_bundle(path, self.root / "candidate", REPO, "0.2.0")

    def test_source_seed_cannot_overwrite_user_watch_data(self):
        files = {
            "ternela/__main__.py": b"pass\n",
            "seed/sources/manifest.json": b"[]\n",
            "seed/workbench/watches.sqlite3": b"replacement database",
        }
        manifest = {
            "format": 1,
            "runtime": 1,
            "python": [3, 15],
            "version": "0.2.0",
            "repository": REPO,
            "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
        }
        files["release.json"] = json.dumps(manifest).encode()
        path = self.root / "seed.tar.gz"
        with tarfile.open(path, "w:gz") as output:
            for name, raw in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(raw)
                output.addfile(entry, io.BytesIO(raw))
        with self.assertRaisesRegex(ValueError, "seed"):
            extract_bundle(path, self.root / "candidate", REPO, "0.2.0")

    def test_duplicate_consent_keys_are_ambiguous_and_default_off(self):
        (self.root / "settings.json").write_text(
            '{"automatic_updates":false,"automatic_updates":true}'
        )
        self.assertFalse(self.service.consent())

    def test_malformed_failed_version_blocks_automatic_application(self):
        save(self.root / "failed-version.json", {"version": []})
        self.assertTrue(self.service.blocked_version("0.2.0"))


if __name__ == "__main__":
    unittest.main()
