# SPDX-License-Identifier: Apache-2.0
"""Update transaction boundaries and consent; no provider calls or live processes."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ternela.local_service import LocalService, read, save


class FakeService(LocalService):
    def __init__(self, state):
        super().__init__(state, state / "unused.tar.gz")
        self.repository = "Alphabetsoup16/ternela"
        self.started = []
        self.fail_target = None
        self.fail_rollback = False
        (state / "data").mkdir()
        (state / "data/sentinel").write_text("original")
        save(state / "current.json", {"version": "0.1.0", "previous": None})
        save(state / "settings.json", {"automatic_updates": False})

    def start_app(self, target):
        self.started.append(target)
        if target != "0.1.0":
            (self.state / "data/sentinel").write_text("candidate changed data")

    def stop_app(self):
        pass

    def healthy(self, target, timeout=45):
        return target != self.fail_target and not (target == "0.1.0" and self.fail_rollback)


class LocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = FakeService(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_default_off_does_not_probe(self):
        with patch.object(self.service, "probe") as probe:
            self.assertEqual(self.service.check(automatic=True), {"status": "disabled"})
        probe.assert_not_called()

    def test_update_success_commits_current_and_keeps_one_backup(self):
        self.service.apply("0.2.0")
        self.assertEqual(self.service.current(), {"version": "0.2.0", "previous": "0.1.0"})
        self.assertFalse((self.root / "transaction.json").exists())
        self.assertTrue((self.root / "pre-update.tar.gz").exists())
        self.assertEqual((self.root / "data/sentinel").read_text(), "candidate changed data")

    def test_candidate_failure_restores_data_before_old_code_starts(self):
        self.service.fail_target = "0.2.0"
        with self.assertRaisesRegex(RuntimeError, "Candidate"):
            self.service.apply("0.2.0")
        self.assertEqual(self.service.started, ["0.2.0", "0.1.0"])
        self.assertEqual(self.service.current()["version"], "0.1.0")
        self.assertEqual((self.root / "data/sentinel").read_text(), "original")
        self.assertFalse((self.root / "transaction.json").exists())

    def test_backup_failure_does_not_start_candidate(self):
        with patch.object(self.service, "backup", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.apply("0.2.0")
        self.assertEqual(self.service.started, ["0.1.0"])
        self.assertEqual((self.root / "data/sentinel").read_text(), "original")

    def test_failed_rollback_retains_recovery_journal(self):
        self.service.fail_target = "0.2.0"
        self.service.fail_rollback = True
        with self.assertRaisesRegex(RuntimeError, "Rollback failed"):
            self.service.apply("0.2.0")
        self.assertTrue((self.root / "transaction.json").exists())

    def test_interrupted_candidate_restores_on_restart(self):
        self.service.backup()
        save(self.root / "transaction.json", {"old": "0.1.0", "target": "0.2.0"})
        (self.root / "data/sentinel").write_text("partial candidate data")
        self.service.recover()
        self.assertEqual((self.root / "data/sentinel").read_text(), "original")
        self.assertEqual(read(self.root / "failed-version.json"), {"version": "0.2.0"})
        self.assertFalse((self.root / "transaction.json").exists())

    def test_interruption_after_commit_preserves_new_data(self):
        self.service.backup()
        save(self.root / "transaction.json", {"old": "0.1.0", "target": "0.2.0"})
        save(self.root / "current.json", {"version": "0.2.0", "previous": "0.1.0"})
        (self.root / "data/sentinel").write_text("committed")
        self.service.recover()
        self.assertEqual((self.root / "data/sentinel").read_text(), "committed")

    def test_corrupt_backup_does_not_clear_recovery_journal(self):
        save(self.root / "transaction.json", {"old": "0.1.0", "target": "0.2.0"})
        (self.root / "pre-update.tar.gz").write_bytes(b"bad")
        with self.assertRaises(Exception):
            self.service.recover()
        self.assertTrue((self.root / "transaction.json").exists())
        self.assertEqual((self.root / "data/sentinel").read_text(), "original")

    def test_backup_refuses_links_outside_data(self):
        (self.root / "data/link").symlink_to(self.root / "settings.json")
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.service.backup()

    def test_backup_digest_detects_changed_bytes_before_restoring(self):
        self.service.backup()
        (self.root / "pre-update.tar.gz").write_bytes(b"a different archive")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.service.restore()
        self.assertEqual((self.root / "data/sentinel").read_text(), "original")

    def test_stop_fences_descendants_even_if_leader_already_exited(self):
        import signal

        service = LocalService(self.root, self.root / "unused.tar.gz")
        child = Mock(pid=12345)
        child.poll.return_value = 17
        service.child = child
        with patch("ternela.local_service.os.killpg") as kill:
            service.stop_app()
        self.assertEqual(
            kill.call_args_list,
            [unittest.mock.call(12345, signal.SIGINT), unittest.mock.call(12345, signal.SIGKILL)],
        )
        self.assertIsNone(service.child)

    def test_provider_failure_keeps_current_service(self):
        for failure in (OSError("offline"), ValueError("invalid JSON"), TimeoutError("timeout")):
            with (
                self.subTest(failure=failure),
                patch.object(self.service, "probe", side_effect=failure),
            ):
                result = self.service.check()
                self.assertEqual(result["status"], "error")
                self.assertEqual(self.service.started, [])
                self.assertEqual(self.service.current()["version"], "0.1.0")

    def test_unknown_release_evidence_cannot_install(self):
        for decision in ("unknown", "pending", "unverifiable"):
            with patch.object(
                self.service, "probe", return_value={"decision": decision, "watch_id": "watch"}
            ):
                result = self.service.check()
                self.assertEqual(
                    result["status"], "no_update" if decision == "pending" else "unavailable"
                )
        self.assertEqual(self.service.started, [])

    def test_bad_download_does_not_stop_app_and_blocks_automatic_retry(self):
        from test_distribution import metadata

        save(self.root / "settings.json", {"automatic_updates": True})
        evidence = {
            "decision": "complete",
            "watch_id": "watch",
            "release": metadata(),
            "expires_at": time.time() + 300,
        }
        with (
            patch.object(self.service, "probe", return_value=evidence),
            patch(
                "ternela.local_service.download_asset", side_effect=ValueError("digest mismatch")
            ) as download,
        ):
            self.assertEqual(self.service.check(automatic=True)["status"], "error")
            # A later network failure cannot clear the suppression of this failed version.
            with patch.object(self.service, "probe", side_effect=OSError("offline")):
                self.service.check(automatic=True)
            self.assertEqual(self.service.check(automatic=True)["status"], "skipped_failed")
            self.assertEqual(download.call_count, 1)
            self.assertEqual(self.service.check()["status"], "error")
            self.assertEqual(download.call_count, 2)
        self.assertEqual(self.service.started, [])

    def test_disable_during_download_cancels_before_service_stops(self):
        from test_distribution import metadata

        save(self.root / "settings.json", {"automatic_updates": True})

        def cancel(*_):
            save(self.root / "settings.json", {"automatic_updates": False})

        with (
            patch.object(
                self.service,
                "probe",
                return_value={
                    "decision": "complete",
                    "release": metadata(),
                    "expires_at": time.time() + 300,
                },
            ),
            patch("ternela.local_service.download_asset", side_effect=cancel),
            patch(
                "ternela.local_service.extract_bundle", side_effect=lambda *args: args[1].mkdir()
            ),
        ):
            self.assertEqual(self.service.check(automatic=True)["status"], "cancelled")
        self.assertEqual(self.service.started, [])

    def test_retention_keeps_current_previous_and_ten_command_results(self):
        save(self.root / "current.json", {"version": "0.2.0", "previous": "0.1.0"})
        for target in ("0.1.0", "0.2.0", "0.3.0"):
            self.service.app(target).mkdir()
        for index in range(15):
            save(self.root / "results" / (str(index) + ".json"), {"status": "done"})
        self.service.prune()
        self.assertEqual({p.name for p in self.service.releases.iterdir()}, {"0.1.0", "0.2.0"})
        self.assertEqual(len(list((self.root / "results").glob("*.json"))), 10)

    def test_corrupt_consent_is_disabled_without_stopping_service(self):
        (self.root / "settings.json").write_text("invalid JSON")
        with patch.object(self.service, "probe") as probe:
            self.assertEqual(self.service.check(automatic=True), {"status": "disabled"})
        probe.assert_not_called()

    def test_stale_or_missing_release_evidence_cannot_download(self):
        from test_distribution import metadata

        for expires in (None, 0, float("inf"), "tomorrow"):
            with (
                patch.object(
                    self.service,
                    "probe",
                    return_value={
                        "decision": "complete",
                        "release": metadata(),
                        "expires_at": expires,
                    },
                ),
                patch("ternela.local_service.download_asset") as download,
            ):
                self.assertEqual(self.service.check()["status"], "error")
            download.assert_not_called()

    def test_another_supervisor_cannot_own_this_folder(self):
        # Use a competing process: same-process advisory-lock semantics vary by filesystem.
        code = "import fcntl,sys; lock=open(sys.argv[1],'a'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); print('locked',flush=True); sys.stdin.read()"
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(self.root / "supervisor.lock")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline(), "locked\n")
            with self.assertRaisesRegex(RuntimeError, "Another supervisor"):
                self.service.run()
        finally:
            try:
                process.communicate(input="", timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()

    def test_atomic_saved_settings_are_plain_json(self):
        self.assertEqual(
            json.loads((self.root / "settings.json").read_text()), {"automatic_updates": False}
        )
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
