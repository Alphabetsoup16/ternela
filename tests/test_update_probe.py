# SPDX-License-Identifier: Apache-2.0
"""The self-update watch uses the same admission, typed evaluation and receipt path."""

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import PROJECT, Store
from ternela import update_probe


class UpdateProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "examples").mkdir()
        shutil.copyfile(PROJECT / "examples/catalog.json", self.root / "examples/catalog.json")

    def tearDown(self):
        self.temporary.cleanup()

    def probe(self, body, status=200):
        responses = {
            "latest_release": [
                {"http_status": status, "body": body, "headers": {}, "headers_complete": True}
            ]
        }
        with (
            patch.object(update_probe, "PROJECT", self.root),
            patch.object(update_probe, "Store", return_value=Store(self.root / "data")),
            patch.object(update_probe, "collect", return_value=(responses, time.time())),
        ):
            return update_probe.probe("0.1.0")

    def test_same_version_pending_then_new_version_complete_with_same_watch(self):
        body = {
            "draft": False,
            "prerelease": False,
            "tag_name": "v0.1.0",
            "published_at": "2026-10-19T10:00:00Z",
        }
        first = self.probe(body)
        self.assertEqual(first["decision"], "pending")
        body = dict(body, tag_name="v0.2.0")
        second = self.probe(body)
        self.assertEqual(second["decision"], "complete")
        self.assertEqual(first["watch_id"], second["watch_id"])
        self.assertGreater(second["expires_at"], time.time())
        self.assertEqual(second["release"], body)

    def test_missing_release_rate_limit_and_provider_failure_remain_unknown(self):
        for status in (404, 403, 429, 0):
            with self.subTest(status=status):
                self.assertEqual(self.probe(None, status)["decision"], "unknown")

    def test_draft_and_prerelease_do_not_complete(self):
        body = {
            "draft": False,
            "prerelease": False,
            "tag_name": "v0.2.0",
            "published_at": "2026-10-19T10:00:00Z",
        }
        for field in ("draft", "prerelease"):
            self.assertEqual(self.probe(dict(body, **{field: True}))["decision"], "pending")

    def test_missing_publication_evidence_remains_unknown(self):
        self.assertEqual(
            self.probe({"draft": False, "prerelease": False, "tag_name": "v0.2.0"})["decision"],
            "unknown",
        )

    def test_observation_retains_receipt_input_and_ordered_revision(self):
        body = {
            "draft": False,
            "prerelease": False,
            "tag_name": "v0.2.0",
            "published_at": "2026-10-19T10:00:00Z",
        }
        first = self.probe(body)
        self.probe(body)
        from workbench.engine import Watches

        engine = Watches(self.root / "data/workbench/watches.sqlite3", Store(self.root / "data"))
        watch = engine.get(first["watch_id"])
        self.assertEqual(watch["revision"], 2)
        self.assertEqual(watch["receipt"]["input"]["latest_release"][0]["body"], body)
        self.assertEqual(watch["receipt"]["mode"], "live public GitHub release update check")
        self.assertIn("Alphabetsoup16/ternela", watch["task"]["task"])
        self.assertNotIn("cli/cli", json.dumps(watch["plan"]["requirements"]))


if __name__ == "__main__":
    unittest.main()
