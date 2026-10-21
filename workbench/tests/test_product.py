# SPDX-License-Identifier: Apache-2.0
"""Failure contracts for recovery, independent delivery and bounded shared reads."""

import gzip
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from expansion_lab.storage import PROJECT, Store
from ternela.connectors import connector_descriptors, recipe_plan, recipe_providers
from workbench.engine import Watches
from workbench.live import ReadBatch, collect
from workbench.monitor import Monitors

TARGET = "br0l2tvcx85d"
OTHER = "brv1bkgrwx7q"


class ProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good = json.loads(
            gzip.decompress((PROJECT / "evals/github_status_v1.json.gz").read_bytes())
        )["cases"][0]["envelope"]

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.now = 1000.0
        self.store = Store(root / "data")
        self.engine = Watches(root / "watches.sqlite3", self.store, lambda: self.now)
        self.watch = self.create()

    def create(self, component=TARGET, ttl=900):
        task, plan, phase = recipe_plan("github_status", {"component_id": component})
        return self.engine.create(task, plan, phase, ttl)

    def submit(self, revision=1, envelope=None, event=None):
        with (
            patch("ternela.connectors.read_json", return_value=deepcopy(envelope or self.good)),
            patch("workbench.live.time.time", return_value=self.now),
        ):
            snapshots, _ = collect(
                self.watch["plan"], self.watch["task"]["context"], "before", "github_status"
            )
        return self.engine.submit(
            self.watch["id"], event or str(revision), revision, snapshots, self.now
        )

    def restart(self):
        self.engine = Watches(self.engine.path, self.store, lambda: self.now)

    def test_descriptors_cover_every_recipe_and_cannot_mutate_registry(self):
        descriptors = connector_descriptors()
        self.assertEqual(
            {c["provider"] for c in descriptors if c["recipe"]}, set(recipe_providers())
        )
        for descriptor in descriptors:
            self.assertTrue(descriptor["documentation"].startswith("https://"))
            if descriptor["recipe"]:
                self.assertTrue(descriptor["recipe"]["hint"])
                self.assertEqual(
                    len({f["key"] for f in descriptor["recipe"]["fields"]}),
                    len(descriptor["recipe"]["fields"]),
                )
        status = next(c for c in descriptors if c["provider"] == "github_status")
        status["recipe"]["fields"][0]["options"].clear()
        self.assertTrue(
            next(c for c in connector_descriptors() if c["provider"] == "github_status")["recipe"][
                "fields"
            ][0]["options"]
        )

    def test_directory_keyset_pagination_restart_and_no_raw_receipt_reads(self):
        self.submit()
        identities = {self.watch["id"], *(self.create()["id"] for _ in range(4))}
        self.restart()
        # Listing must not decompress a receipt or plan to display a saved watch.
        original = self.store.get

        def task_only(ref, **kwargs):
            value = original(ref, **kwargs)
            self.assertNotIn("observations", value)
            self.assertNotIn("requests", value)
            return value

        found, cursor = [], ""
        with patch.object(self.store, "get", side_effect=task_only):
            while True:
                page = self.engine.directory(cursor, 2)
                found.extend(item["id"] for item in page["items"])
                if not page["next"]:
                    break
                cursor = page["next"]
        self.assertEqual(set(found), identities)
        self.assertEqual(len(found), len(identities))

    def test_directory_reports_expiry_without_reusing_old_ready(self):
        self.submit()
        self.now += 901
        item = self.engine.directory()["items"][0]
        self.assertEqual(item["status"], "unknown")

    def test_upgrade_preserves_old_watches_and_outbox_without_inventing_usage(self):
        self.submit()
        with self.engine.connect() as db:
            db.execute("ALTER TABLE watches DROP COLUMN decision_status")
            db.execute("DROP TABLE consumers")
            db.execute("DROP TABLE watch_usage")
            # Reconstruct the actual pre-versioning database, not a corrupted v3 file.
            db.execute("PRAGMA user_version=0")
            db.execute("DROP TABLE schema_migrations")
        self.restart()
        self.assertEqual(self.engine.directory()["items"][0]["status"], "ready")
        self.assertEqual(len(self.engine.deliveries(self.watch["id"], "new-agent")), 1)
        self.assertEqual(self.engine.usage()["observations"], 0)
        self.submit(2)
        self.assertEqual(self.engine.usage()["observations"], 1)

    def test_failed_receipt_write_cannot_advance_usage_or_delivery(self):
        with patch.object(self.store, "put", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.submit()
        self.assertEqual(self.engine.usage()["observations"], 0)
        self.assertEqual(self.engine.deliveries(self.watch["id"], "agent"), [])
        self.submit()
        self.assertEqual(self.engine.usage()["observations"], 1)

    def test_bad_directory_inputs_are_rejected(self):
        for cursor, limit in [("' OR 1=1", 2), ([], 2), ("", 0), ("", 101), ("", True)]:
            with self.subTest(cursor=cursor, limit=limit), self.assertRaises(ValueError):
                self.engine.directory(cursor, limit)

    def test_consumer_reconnects_before_and_after_ack_and_is_independent(self):
        self.submit()
        identity = self.watch["id"]
        message = self.engine.deliveries(identity, "agent-a")[0]
        self.engine.ack(identity, message["id"])  # UI acknowledgement is independent.
        self.restart()
        self.assertEqual(self.engine.deliveries(identity, "agent-a")[0]["id"], message["id"])
        self.engine.acknowledge_delivery(identity, "agent-a", message["id"])
        self.restart()
        self.assertEqual(self.engine.deliveries(identity, "agent-a"), [])
        self.assertEqual(self.engine.deliveries(identity, "agent-b")[0]["id"], message["id"])
        self.engine.acknowledge_delivery(identity, "agent-a", message["id"])  # Idempotent retry.

    def test_consumer_cannot_skip_a_decision_or_ack_another_watch(self):
        self.submit()
        self.now += 901
        identity = self.watch["id"]
        messages = self.engine.deliveries(identity, "agent")
        self.assertEqual(len(messages), 2)
        with self.assertRaises(ValueError):
            self.engine.acknowledge_delivery(identity, "agent", messages[1]["id"])
        with self.assertRaises(KeyError):
            self.engine.acknowledge_delivery(self.create()["id"], "agent", messages[0]["id"])
        self.assertEqual(self.engine.deliveries(identity, "agent")[0]["id"], messages[0]["id"])

    def test_concurrent_duplicate_ack_does_not_skip_next_delivery(self):
        self.submit()
        self.now += 901
        identity = self.watch["id"]
        messages = self.engine.deliveries(identity, "agent")
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(
                workers.map(
                    lambda _: self.engine.acknowledge_delivery(
                        identity, "agent", messages[0]["id"]
                    ),
                    range(2),
                )
            )
        self.assertEqual(
            [m["id"] for m in self.engine.deliveries(identity, "agent")], [messages[1]["id"]]
        )

    def test_historical_ready_does_not_become_current_authorization(self):
        self.submit()
        self.now += 901
        identity = self.watch["id"]
        historical = self.engine.deliveries(identity, "agent")[0]
        self.assertEqual(historical["receipt"]["result"]["status"], "ready")
        self.assertEqual(self.engine.status(identity)["receipt"]["result"]["status"], "unknown")

    def test_consumer_validation_does_not_create_state(self):
        for name in ("", "x" * 65, "space name", "a/b", None, "é"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.engine.deliveries(self.watch["id"], name)
        with self.engine.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM consumers").fetchone()[0], 0)

    def test_usage_deduplicates_events_and_separates_expiry_from_reads(self):
        self.submit()
        self.submit()
        first = self.engine.usage(self.watch["id"])
        self.assertEqual(
            (first["observations"], first["read_attempts"], first["decision_changes"]), (1, 1, 1)
        )
        self.submit(2)
        self.now += 901
        self.engine.expire()
        self.engine.expire()
        self.restart()
        usage = self.engine.usage(self.watch["id"])
        self.assertEqual(
            (
                usage["observations"],
                usage["read_attempts"],
                usage["decision_changes"],
                usage["expiry_events"],
            ),
            (2, 2, 2, 1),
        )
        self.assertEqual(usage, self.engine.usage())

    def test_rejected_observation_does_not_count_as_accepted_work(self):
        self.submit(2)
        self.submit(1)
        self.assertEqual(self.engine.usage(self.watch["id"])["observations"], 1)

    def test_unknown_read_is_accounted_without_becoming_success(self):
        self.submit(envelope={"http_status": 503, "body": None})
        usage = self.engine.usage(self.watch["id"])
        self.assertEqual(usage["unknown_observations"], 1)
        self.assertEqual(usage["read_attempts"], 1)

    def test_two_different_conditions_share_one_response_and_evaluate_separately(self):
        other = self.create(OTHER)
        envelope = deepcopy(self.good)
        for row in envelope["body"]["components"]:
            if row["id"] == OTHER:
                row["status"] = "major_outage"
        self.assertIn(OTHER, [c["id"] for c in envelope["body"]["components"]])
        monitors = Monitors(self.engine)
        for identity in (self.watch["id"], other["id"]):
            monitors.start(identity, 60)
        with (
            patch("ternela.connectors.read_json", return_value=envelope) as reader,
            patch("workbench.live.time.time", return_value=self.now),
        ):
            result = monitors.run_batch()
        self.assertEqual(result, {"watches": 2, "network_reads": 1, "shared_reads": 1})
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(
            self.engine.status(self.watch["id"])["receipt"]["result"]["status"], "ready"
        )
        self.assertEqual(self.engine.status(other["id"])["receipt"]["result"]["status"], "blocked")
        self.assertEqual(
            (self.engine.usage()["network_reads"], self.engine.usage()["shared_reads"]), (1, 1)
        )
        self.now += 60
        with (
            patch("ternela.connectors.read_json", return_value=deepcopy(self.good)) as fresh,
            patch("workbench.live.time.time", return_value=self.now),
        ):
            Monitors(self.engine).run_batch()
        self.assertEqual(fresh.call_count, 1)  # New batch really reacquires the response.
        self.assertEqual(self.engine.status(other["id"])["receipt"]["result"]["status"], "ready")

    def test_pause_during_shared_acquisition_fences_only_the_paused_watch(self):
        second = self.create()
        monitors = Monitors(self.engine)
        for identity in (self.watch["id"], second["id"]):
            monitors.start(identity, 60)
        first_id = min(self.watch["id"], second["id"])

        def reader(*args):
            monitors.pause(first_id)
            return deepcopy(self.good)

        with (
            patch("ternela.connectors.read_json", side_effect=reader),
            patch("workbench.live.time.time", return_value=self.now),
        ):
            monitors.run_batch()
        self.assertEqual(self.engine.get(first_id)["revision"], 0)
        other_id = next(i for i in (self.watch["id"], second["id"]) if i != first_id)
        self.assertEqual(self.engine.status(other_id)["receipt"]["result"]["status"], "ready")
        self.assertEqual(self.engine.usage()["network_reads"], 1)

    def test_batch_limit_does_not_claim_extra_watches(self):
        monitors = Monitors(self.engine, lambda *args: ({}, self.now))
        for _ in range(3):
            monitors.start(self.create()["id"], 60)
        self.assertEqual(monitors.run_batch(limit=2)["watches"], 2)
        self.assertTrue(monitors.run_once())

    def test_short_ttl_forces_new_read_instead_of_reusing_old_batch(self):
        monitors = Monitors(self.engine)
        monitors.start(self.watch["id"], 60)
        batch = ReadBatch(reader=Mock(return_value=deepcopy(self.good)))
        batch.started = self.now - 901
        batch.cache[
            ("www.githubstatus.com", "https://www.githubstatus.com/api/v2/summary.json")
        ] = deepcopy(self.good)
        with patch("workbench.live.time.time", return_value=self.now):
            monitors.run_once(_batch=batch)
        self.assertEqual(batch.network_reads, 1)
        self.assertEqual(batch.shared_reads, 0)
        self.assertEqual(self.engine.get(self.watch["id"])["receipt"]["observed_at"], self.now)


class ReadBatchTests(unittest.TestCase):
    def test_exact_identity_and_copy_isolation(self):
        reader = Mock(return_value={"http_status": 200, "headers": {}, "body": {"value": 1}})
        batch = ReadBatch(reader)
        batch.read("https://example.org/a", "example.org", 1)["body"]["value"] = 99
        self.assertEqual(batch.read("https://example.org/a", "example.org", 1)["body"]["value"], 1)
        batch.read("https://example.org/a?x=1", "example.org", 1)
        batch.read("https://example.org/b", "example.org", 1)
        batch.read("https://other.org/a", "other.org", 1)
        self.assertEqual(reader.call_count, 4)
        self.assertEqual(batch.shared_reads, 1)

    def test_failures_and_cache_control_are_never_reused(self):
        for envelope in (
            {"http_status": 503},
            {"http_status": 404},
            {"http_status": 200, "headers": {"cache-control": "private, no-store"}},
            {"http_status": 200, "headers": {"cache-control": "no-cache"}},
        ):
            with self.subTest(envelope=envelope):
                reader = Mock(return_value=envelope)
                batch = ReadBatch(reader)
                for _ in range(2):
                    batch.read("https://example.org/a", "example.org", 1)
                self.assertEqual(reader.call_count, 2)

    def test_expired_batch_never_calls_network_or_returns_cached_success(self):
        reader = Mock(return_value={"http_status": 200})
        batch = ReadBatch(reader)
        batch.read("https://example.org/a", "example.org", 1)
        batch.deadline = 0
        with self.assertRaises(ValueError):
            batch.read("https://example.org/a", "example.org", 1)
        self.assertEqual(reader.call_count, 1)

    def test_cache_is_bounded_and_oversized_response_is_not_reused(self):
        reader = Mock(return_value={"http_status": 200, "body": "x" * (513 * 1024)})
        batch = ReadBatch(reader)
        for _ in range(2):
            batch.read("https://example.org/a", "example.org", 1)
        self.assertEqual((reader.call_count, batch.bytes), (2, 0))
        reader.return_value = {"http_status": 200, "body": "x" * (256 * 1024)}
        for n in range(140):
            batch.read(f"https://example.org/{n}", "example.org", 1)
        self.assertLessEqual(batch.bytes, 4 * 1024 * 1024)
        self.assertLessEqual(len(batch.cache), 128)

    def test_reader_exception_counts_attempt_and_is_not_cached(self):
        reader = Mock(side_effect=[OSError("offline"), {"http_status": 200}])
        batch = ReadBatch(reader)
        with self.assertRaises(OSError):
            batch.read("https://example.org/a", "example.org", 1)
        self.assertEqual(batch.read("https://example.org/a", "example.org", 1)["http_status"], 200)
        self.assertEqual((batch.network_reads, batch.shared_reads), (2, 0))
