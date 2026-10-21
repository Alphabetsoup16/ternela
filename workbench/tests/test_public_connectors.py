# SPDX-License-Identifier: Apache-2.0
"""Offline behavioral checks for anonymous registry acquisition and its evidence view."""

import base64
import gzip
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import Store
from ternela.connectors import REGISTRIES, release_plan, valid_integrity
from ternela.transport import decode_body, read_json
from ternela.validation import admit
from workbench.engine import Watches, observe
from workbench.live import collect
from workbench.monitor import Monitors


def metadata(provider):
    if provider == "pypi":
        return {
            "info": {"name": "My_Package", "version": "1.2.3"},
            "urls": [{"filename": "package.whl", "yanked": False, "digests": {"sha256": "a" * 64}}],
        }
    return {
        "name": "@scope/package",
        "version": "1.2.3",
        "dist": {
            "integrity": "sha512-" + base64.b64encode(b"a" * 64).decode(),
            "tarball": "https://registry.npmjs.org/package.tgz",
        },
    }


class PublicConnectorTests(unittest.TestCase):
    def plan(self, provider):
        return release_plan(
            provider, "My_Package" if provider == "pypi" else "@scope/package", "1.2.3"
        )

    def acquire(self, provider, body=None, status=200):
        task, plan = self.plan(provider)
        envelope = {
            "http_status": status,
            "body": metadata(provider) if body is None else body,
            "headers": {},
            "headers_complete": True,
        }
        with patch("ternela.connectors.read_json", return_value=envelope):
            captured, _ = collect(plan, task["context"], "after", provider)
        result, _ = observe(plan, task["context"], captured, "after")
        return result, captured

    def test_reviewed_recipes_are_admitted_and_keep_provenance_explicit(self):
        for provider in REGISTRIES:
            with self.subTest(provider=provider):
                task, plan = self.plan(provider)
                source = admit(plan, task)["pinned_source"]
                self.assertEqual(source["kind"], "reviewed_local_evidence_view")
                self.assertIn("Metadata only", source["limitation"])
                self.assertEqual(len(source["adapter_sha256"]), 64)

    def test_exact_registry_reads_complete_and_preserve_raw_evidence(self):
        for provider in REGISTRIES:
            with self.subTest(provider=provider):
                result, captured = self.acquire(provider)
                self.assertEqual(result["status"], "complete")
                self.assertEqual(captured["release"][0]["source_body"], metadata(provider))
                self.assertEqual(captured["release"][0]["view_contract"]["provider"], provider)

    def test_wrong_identity_or_version_never_completes(self):
        for provider in REGISTRIES:
            for key, value in (("name", "other"), ("version", "1.2.4")):
                body = metadata(provider)
                (body["info"] if provider == "pypi" else body)[key] = value
                self.assertEqual(self.acquire(provider, body)[0]["status"], "pending")

    def test_pypi_empty_or_yanked_files_remain_pending(self):
        for files in ([], [dict(metadata("pypi")["urls"][0], yanked=True)]):
            self.assertEqual(
                self.acquire("pypi", dict(metadata("pypi"), urls=files))[0]["status"], "pending"
            )

    def test_missing_metadata_is_unknown_and_keeps_original_http_status(self):
        for provider in REGISTRIES:
            body = metadata(provider)
            del body["urls" if provider == "pypi" else "dist"]
            result, captured = self.acquire(provider, body)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(captured["release"][0]["source_http_status"], 200)
            self.assertEqual(captured["release"][0]["source_body"], body)

    def test_provider_errors_never_complete(self):
        for provider in REGISTRIES:
            for status in (404, 429, 503):
                with self.subTest(provider=provider, status=status):
                    self.assertEqual(self.acquire(provider, status=status)[0]["status"], "unknown")

    def test_invalid_integrity_and_tarball_metadata_fail_checks(self):
        for key, value in (
            ("integrity", "sha512-a"),
            ("tarball", "http://registry.npmjs.org/a"),
            ("tarball", "https:///a"),
            ("tarball", "https://user:pass@example.com/a"),
        ):
            body = metadata("npm")
            body["dist"][key] = value
            self.assertEqual(self.acquire("npm", body)[0]["status"], "pending")
        self.assertFalse(valid_integrity("sha512-" + base64.b64encode(b"a").decode()))
        self.assertTrue(valid_integrity(metadata("npm")["dist"]["integrity"]))

    def test_invalid_registry_targets_rejected_before_network(self):
        with patch("ternela.connectors.read_json") as network:
            for package, version in (
                ("a/../../b", "1.0.0"),
                ("foo", "latest"),
                ("foo", "^1.0.0"),
                ("foo", "1.0.0-01"),
                ("foo", "01.0.0"),
                ("foo", "1.0.0/path"),
            ):
                with self.assertRaises(ValueError):
                    release_plan("npm", package, version)
            network.assert_not_called()

    def test_scoped_npm_names_are_encoded_in_one_path_segment(self):
        task, plan = self.plan("npm")
        with patch("ternela.connectors.read_json", return_value={"http_status": 404}) as network:
            collect(plan, task["context"], "after", "npm")
        self.assertEqual(
            network.call_args.args[:2],
            ("https://registry.npmjs.org/%40scope%2Fpackage/1.2.3", "registry.npmjs.org"),
        )

    def test_transport_disallows_foreign_origins_and_redirect_credentials(self):
        for url in (
            "http://pypi.org/a",
            "https://pypi.org.evil/a",
            "https://user@pypi.org/a",
            "https://pypi.org/a#x",
        ):
            with self.assertRaises(ValueError):
                read_json(url, "pypi.org")

    def test_gzip_decoding_is_bounded_and_rejects_truncated_data(self):
        self.assertEqual(decode_body(gzip.compress(b'{"x":1}'), "gzip"), b'{"x":1}')
        with self.assertRaises(ValueError):
            decode_body(gzip.compress(b"x" * 1000), "gzip", 100)
        with self.assertRaises((OSError, EOFError)):
            decode_body(gzip.compress(b"{}")[:-4], "gzip")
        with self.assertRaises(ValueError):
            decode_body(b"{}", "br")

    def test_live_execution_enforces_preservations_before_transport(self):
        from workbench.demo import catalog

        entry = next(item for item in catalog() if item["task"]["id"] == "n01")
        with patch(
            "workbench.live.connector_request", return_value={"http_status": 503, "body": None}
        ) as network:
            captured, _ = collect(entry["plan"], entry["task"]["context"], "before", "stripe")
        self.assertEqual(network.call_count, 1)
        self.assertEqual(len(captured), 1)

    def test_provider_pause_does_not_block_another_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Watches(root / "state.sqlite3", Store(root / "data"), lambda: 1000)
            monitors = Monitors(engine)
            watches = {}
            for provider in REGISTRIES:
                task, plan = self.plan(provider)
                watches[provider] = engine.create(task, plan)
                monitors.start(watches[provider]["id"])
            with engine.connect() as db:
                db.execute("UPDATE monitor_limits SET not_before=2000 WHERE provider='npm-public'")
            self.assertEqual(monitors.claim()["watch_id"], watches["pypi"]["id"])
            self.assertIsNone(monitors.claim())
            # Pause persists after constructing another worker.
            self.assertIsNone(Monitors(engine).claim())

    def test_compact_status_preserves_decision_without_raw_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Watches(root / "state.sqlite3", Store(root / "data"))
            task, plan = self.plan("npm")
            watch = engine.create(task, plan)
            self.assertIsNone(engine.status(watch["id"])["receipt"])
            _, captured = self.acquire("npm")
            engine.submit(watch["id"], "one", 1, captured)
            full, small = engine.get(watch["id"]), engine.status(watch["id"])
            self.assertEqual(small["receipt"]["result"], full["receipt"]["result"])
            self.assertEqual(small["receipt"]["read_attempts"], 1)
            for key in ("task", "plan", "active_plan"):
                self.assertNotIn(key, small)
            for key in ("input", "observations", "source_evidence"):
                self.assertNotIn(key, small["receipt"])
            self.assertEqual(
                engine.outbox(watch["id"], compact=True)[0]["receipt"], small["receipt"]
            )
