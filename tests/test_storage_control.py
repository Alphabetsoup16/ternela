# SPDX-License-Identifier: Apache-2.0
"""Transfers must verify bytes before replacement and recover interrupted swaps."""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ternela.storage_control import WORK, export_state, import_state, locked, recover


class StorageControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "state"
        self.state.mkdir()
        (self.state / "data").mkdir()
        (self.state / "data/evidence.gz").write_bytes(b"retained evidence")
        (self.state / "settings.json").write_text('{"automatic_updates": false}')
        app = self.state / "releases/0.1.0"
        app.mkdir(parents=True)
        (app / "data").symlink_to("/state/data")

    def archive(self):
        output = io.BytesIO()
        with locked(self.state):
            export_state(self.state, output)
        return output.getvalue()

    def test_round_trip_preserves_preferences_evidence_and_release_link(self):
        raw = self.archive()
        (self.state / "data/evidence.gz").write_bytes(b"later evidence")
        (self.state / "extra.json").write_text("{}")
        with locked(self.state):
            import_state(self.state, io.BytesIO(raw))
        self.assertEqual((self.state / "data/evidence.gz").read_bytes(), b"retained evidence")
        self.assertFalse((self.state / "extra.json").exists())
        self.assertFalse(
            json.loads((self.state / "settings.json").read_text())["automatic_updates"]
        )
        self.assertEqual((self.state / "releases/0.1.0/data").readlink(), Path("/state/data"))

    def test_tampered_payload_does_not_change_existing_state(self):
        raw = self.archive()
        output = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
            with tarfile.open(fileobj=output, mode="w:gz") as destination:
                for entry in source:
                    body = source.extractfile(entry).read() if entry.isfile() else None
                    if entry.name == "data/evidence.gz":
                        body = b"forged evidence"
                        entry.size = len(body)
                    destination.addfile(entry, io.BytesIO(body) if body is not None else None)
        with self.assertRaisesRegex(ValueError, "integrity"):
            import_state(self.state, io.BytesIO(output.getvalue()))
        self.assertEqual((self.state / "data/evidence.gz").read_bytes(), b"retained evidence")
        self.assertFalse((self.state / WORK).exists())

    def test_unsafe_paths_links_and_duplicate_members_are_rejected(self):
        for name, target in (
            ("../outside", None),
            ("data/link", "/etc/passwd"),
            ("data/link", "../outside"),
            ("supervisor.lock", None),
        ):
            with self.subTest(name=name, target=target):
                output = io.BytesIO()
                with tarfile.open(fileobj=output, mode="w:gz") as archive:
                    entry = tarfile.TarInfo(name)
                    if target:
                        entry.type, entry.linkname = tarfile.SYMTYPE, target
                    archive.addfile(entry)
                with self.assertRaises(ValueError):
                    import_state(self.state, io.BytesIO(output.getvalue()))
                self.assertEqual(
                    (self.state / "data/evidence.gz").read_bytes(), b"retained evidence"
                )

    def test_hardlinked_regular_files_round_trip(self):
        import os

        os.link(self.state / "data/evidence.gz", self.state / "data/duplicate.gz")
        raw = self.archive()
        import_state(self.state, io.BytesIO(raw))
        self.assertEqual((self.state / "data/duplicate.gz").read_bytes(), b"retained evidence")

    def test_truncated_archive_preserves_current_state(self):
        raw = self.archive()
        with self.assertRaises((EOFError, tarfile.TarError)):
            import_state(self.state, io.BytesIO(raw[:30]))
        self.assertEqual((self.state / "data/evidence.gz").read_bytes(), b"retained evidence")

    def test_transfer_size_limits_are_symmetric_and_leave_state_intact(self):
        raw = self.archive()
        with patch("ternela.storage_control.MAX_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "limits"):
                self.archive()
            with self.assertRaisesRegex(ValueError, "limits"):
                import_state(self.state, io.BytesIO(raw))
        self.assertEqual((self.state / "data/evidence.gz").read_bytes(), b"retained evidence")

    def test_duplicate_archive_member_is_rejected(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for _ in range(2):
                archive.addfile(tarfile.TarInfo("duplicate"))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            import_state(self.state, io.BytesIO(output.getvalue()))

    def test_transfer_refuses_a_running_supervisor(self):
        with locked(self.state):
            with self.assertRaisesRegex(RuntimeError, "Stop Ternela"):
                with locked(self.state):
                    self.fail("Competing lock acquired")

    def test_unexpected_source_symlink_cannot_be_exported(self):
        (self.state / "data/secret").symlink_to("/etc/passwd")
        with self.assertRaises(ValueError):
            self.archive()

    def test_interrupted_swap_recovers_original_files(self):
        raw = self.archive()
        (self.state / "data/evidence.gz").write_bytes(b"current evidence")
        rename = Path.rename

        def fail(source, destination):
            if source == self.state / WORK / "new/settings.json":
                raise OSError("simulated replacement failure")
            return rename(source, destination)

        with patch.object(Path, "rename", fail):
            with self.assertRaisesRegex(OSError, "replacement failure"):
                import_state(self.state, io.BytesIO(raw))
        self.assertEqual((self.state / "data/evidence.gz").read_bytes(), b"current evidence")
        self.assertTrue((self.state / "settings.json").exists())

    def test_restart_rolls_back_a_partially_replaced_tree(self):
        work = self.state / WORK
        (work / "old").mkdir(parents=True)
        (self.state / "data").rename(work / "old/data")
        (self.state / "data").mkdir()
        (self.state / "data/partial").write_text("uncommitted")
        (work / "journal.json").write_text(
            json.dumps({"phase": "replacing", "old": ["data"], "new": ["data"]})
        )
        recover(self.state)
        self.assertEqual((self.state / "data/evidence.gz").read_bytes(), b"retained evidence")
        self.assertFalse((self.state / "data/partial").exists())

    def test_committed_swap_cleanup_preserves_new_tree(self):
        work = self.state / WORK
        (work / "old").mkdir(parents=True)
        (work / "old/settings.json").write_text("old")
        (work / "journal.json").write_text(
            json.dumps({"phase": "committed", "old": ["settings.json"], "new": ["settings.json"]})
        )
        recover(self.state)
        self.assertFalse(
            json.loads((self.state / "settings.json").read_text())["automatic_updates"]
        )
        self.assertFalse(work.exists())
