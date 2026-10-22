# SPDX-License-Identifier: Apache-2.0
"""Installed resource/state separation and expiry after consolidated reads."""

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ternela import acquisition, paths, sources
from ternela.connectors import recipe_plan
from ternela.storage import Store
from ternela.watches import Watches


class PackagePathsTests(unittest.TestCase):
    def test_explicit_state_path_is_independent_of_install_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"TERNELA_DATA": temporary}):
                self.assertEqual(paths.data_root(), Path(temporary).resolve())

    def test_installed_default_stays_outside_site_packages(self):
        with patch.object(paths, "CHECKOUT", False), patch.dict(os.environ, {}, clear=True):
            root = paths.data_root()
            self.assertTrue(root.is_relative_to(Path.home()))
            self.assertFalse(root.is_relative_to(paths.PROJECT))

    def test_read_only_seed_is_fallback_and_explicit_override_remains_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(paths, "DATA", root):
                self.assertEqual(
                    paths.source("sources/manifest.json"),
                    paths.resource("data/sources/manifest.json"),
                )
                override = root / "sources/manifest.json"
                override.parent.mkdir()
                override.write_text("corrupt input must not fall back to the seed")
                self.assertEqual(paths.source("sources/manifest.json"), override)

    def test_supplement_write_becomes_visible_without_modifying_seed(self):
        original = sources.MANIFEST
        records = json.loads(original.read_text())
        record = records[0]
        raw = Store(original.parent.parent).get(record["raw_document"])
        before = original.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            local = data / "sources/supplements.json"

            def evidence_path(name):
                path = data / name
                return path if path.exists() else paths.resource("data/" + name)

            with (
                patch.object(sources, "DATA", data),
                patch.object(sources, "MANIFEST", local),
                patch.object(sources, "source", side_effect=evidence_path),
                patch.object(sources, "Store", side_effect=lambda root=None: Store(root or data)),
            ):
                sources.ingest(
                    record["provider"],
                    record["operation"],
                    record["url"],
                    raw,
                    "2000-01-01T00:00:00+00:00",
                )
                loaded = sources.SourceIndex(record["provider"])
                self.assertEqual(loaded.supplements[0]["fetched_at"], "2000-01-01T00:00:00+00:00")
                self.assertEqual(original.read_bytes(), before)

    def test_snapshot_read_publishes_expiry_once_and_retains_delivery_on_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = [1000.0]
            store = Store(root / "data")
            engine = Watches(root / "watches.sqlite3", store, lambda: now[0])
            target = "br0l2tvcx85d"
            task, plan, phase = recipe_plan("github_status", {"component_id": target})
            watch = engine.create(task, plan, phase, ttl=2)
            fixture = paths.PROJECT / "evals/github_status_v1.json.gz"
            envelope = json.loads(gzip.decompress(fixture.read_bytes()))["cases"][0]["envelope"]
            with (
                patch("ternela.connectors.read_json", return_value=envelope),
                patch("ternela.acquisition.time.time", return_value=now[0]),
            ):
                observed = acquisition.acquire(plan, task["context"], phase, "github_status")
            engine.submit(watch["id"], "ready", 1, observed, now[0])
            self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "ready")
            now[0] += 2
            engine.snapshot(watch["id"])
            restarted = Watches(root / "watches.sqlite3", store, lambda: now[0])
            self.assertEqual(restarted.get(watch["id"])["receipt"]["result"]["status"], "unknown")
            self.assertEqual(restarted.usage(watch["id"])["expiry_events"], 1)
            self.assertEqual(len(restarted.outbox(watch["id"])), 2)


if __name__ == "__main__":
    unittest.main()
