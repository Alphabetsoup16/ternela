# SPDX-License-Identifier: Apache-2.0
"""Failure contracts for single preparation, commit fences, migration and import compatibility."""

import gzip
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

import docs_lab.v2.runtime as old_evaluation
import expansion_lab.schema as old_schema
import expansion_lab.storage as old_storage
import workbench.client as old_client
import workbench.engine as old_watches
import workbench.live as old_acquisition
import workbench.monitor as old_monitor
from ternela import Client, acquisition, client, evaluation, monitor, observations, storage, watches
from ternela.check import boundary_issues
from ternela.connectors import CONNECTORS, ConnectorRegistry, recipe_plan
from ternela.contracts import FrozenJSON, LeaseFence, read_record, write_record
from ternela.database import Database
from ternela.errors import (
    EventConflict,
    LeaseExpired,
    ObservationConflict,
    RecipeArgumentsError,
    UnsupportedVersion,
)
from ternela.migrations import MIGRATIONS, SCHEMA_VERSION, Migration, migrate
from ternela.plan_schema import PLAN
from ternela.storage import PROJECT, Store
from ternela.watches import Watches

TARGET = "br0l2tvcx85d"
OTHER = "brv1bkgrwx7q"


class ArchitectureTests(unittest.TestCase):
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
        self.engine = Watches(root / "w.sqlite3", self.store, lambda: self.now)
        self.watch = self.create()

    def create(self, component=TARGET, ttl=120):
        task, plan, phase = recipe_plan("github_status", {"component_id": component})
        return self.engine.create(task, plan, phase, ttl)

    def acquire(self):
        with (
            patch("ternela.connectors.read_json", return_value=deepcopy(self.good)),
            patch("ternela.acquisition.time.time", return_value=self.now),
        ):
            return acquisition.acquire(
                self.watch["plan"], self.watch["task"]["context"], "before", "github_status"
            )

    def prepared(self, revision=1, event=None):
        return self.engine.prepare(
            self.watch["id"], event or str(revision), revision, self.acquire(), self.now
        )

    def test_legacy_imports_are_the_same_modules_and_public_classes(self):
        for old, new in (
            (old_client, client),
            (old_watches, watches),
            (old_acquisition, acquisition),
            (old_monitor, monitor),
            (old_evaluation, evaluation),
            (old_storage, storage),
        ):
            self.assertIs(old, new)
        self.assertIs(Client, client.ObservationClient)
        self.assertEqual(PLAN, old_schema.PLAN)

    def test_core_imports_and_source_loading_do_not_import_interfaces_or_experiments(self):
        code = "import sys; import ternela.watches,ternela.monitor; from ternela.connectors import source_index; source_index('github'); assert not [n for n in sys.modules if n.split('.')[0]=='workbench' or n.split('.')[0].endswith('_lab')]"
        subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=PROJECT,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_new_transitive_core_dependency_cannot_import_a_lab(self):
        root = Path(self.temporary.name) / "policy"
        (root / "ternela").mkdir(parents=True)
        (root / "ternela/watches.py").write_text("from .helper import value\n")
        (root / "ternela/helper.py").write_text("from docs_lab.runtime import evaluate\n")
        self.assertTrue(any("stable runtime" in issue for issue in boundary_issues(root)))

    def test_package_initializers_are_part_of_the_dependency_boundary(self):
        root = Path(self.temporary.name) / "package-policy"
        (root / "ternela").mkdir(parents=True)
        (root / "ternela/__init__.py").write_text("from workbench.client import Client\n")
        self.assertTrue(any("stable runtime" in issue for issue in boundary_issues(root)))

    def test_new_provider_cannot_import_shared_scheduler(self):
        root = Path(self.temporary.name) / "policy"
        (root / "ternela/providers").mkdir(parents=True)
        (root / "ternela/providers/new.py").write_text("from ternela.monitor import Monitors\n")
        self.assertTrue(any("shared runtime" in issue for issue in boundary_issues(root)))

    def test_live_acquisition_and_commit_evaluate_once_total(self):
        with patch("ternela.observations.evaluate", wraps=observations.evaluate) as evaluate:
            prepared = self.prepared()
            result = self.engine.commit(prepared)
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(result["receipt"]["result"]["status"], "ready")

    def test_caller_snapshots_are_also_evaluated_once_outside_commit(self):
        captured = self.acquire().snapshots.value()
        with patch("ternela.observations.evaluate", wraps=observations.evaluate) as evaluate:
            prepared = self.engine.prepare(self.watch["id"], "manual", 1, captured, self.now)
            self.assertEqual(evaluate.call_count, 1)
            with patch("ternela.watches.observe", side_effect=AssertionError("commit reevaluated")):
                self.engine.commit(prepared)
        self.assertEqual(evaluate.call_count, 1)

    def test_preparation_does_not_hold_the_sqlite_writer_lock(self):
        original = observations.evaluate

        def verify_unlocked(*args):
            with self.engine.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE lock_probe(value INTEGER)")
            return original(*args)

        # A second connection can begin a write even inside the evaluator.
        with patch("ternela.observations.evaluate", side_effect=verify_unlocked):
            self.prepared()

    def test_preparation_does_not_publish_receipts_or_usage(self):
        self.prepared()
        self.assertIsNone(self.engine.get(self.watch["id"])["receipt"])
        self.assertEqual(self.engine.outbox(self.watch["id"]), [])
        self.assertEqual(self.engine.usage()["observations"], 0)

    def test_mutating_caller_input_or_decoded_preparation_cannot_change_commit(self):
        captured = self.acquire().snapshots.value()
        prepared = self.engine.prepare(self.watch["id"], "manual", 1, captured, self.now)
        captured.clear()
        prepared.receipt.value()["result"]["status"] = "complete"
        result = self.engine.commit(prepared)
        self.assertEqual(result["receipt"]["result"]["status"], "ready")
        with self.assertRaises(FrozenInstanceError):
            prepared.revision = 9
        with self.assertRaises(TypeError):
            FrozenJSON(bytearray(b"{}"))

    def test_immutable_json_validates_once_and_preserves_exact_integers(self):
        from ternela import contracts

        with patch("ternela.contracts.freeze_json", wraps=contracts.freeze_json) as validate:
            value = FrozenJSON.capture({"quantity": 9007199254740993})
            for _ in range(5):
                self.assertEqual(value.value()["quantity"], 9007199254740993)
        self.assertEqual(validate.call_count, 1)
        for bad in ({"x": float("nan")}, {"x": object()}, {1: "invalid JSON key"}):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                FrozenJSON.capture(bad)
        for raw in (b'{"x":NaN}', b'{"x":1,"x":2}', b"{"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                FrozenJSON(raw)

    def test_valid_provider_nesting_is_not_rejected_by_internal_receipt_wrappers(self):
        from ternela.storage import canonical, strict_json

        nested = "leaf"
        for _ in range(60):
            nested = {"child": nested}
        envelope = deepcopy(self.good)
        envelope["body"]["unrelated"] = nested
        strict_json(canonical(envelope["body"]))  # Still inside the external 64-level bound.
        with (
            patch("ternela.connectors.read_json", return_value=envelope),
            patch("ternela.acquisition.time.time", return_value=self.now),
        ):
            acquired = acquisition.acquire(
                self.watch["plan"], self.watch["task"]["context"], "before", "github_status"
            )
        result = self.engine.submit(self.watch["id"], "nested", 1, acquired, self.now)
        self.assertEqual(result["receipt"]["result"]["status"], "ready")
        for _ in range(69):
            nested = {"child": nested}
        with self.assertRaises(ValueError):
            FrozenJSON.capture(nested)

    def test_acquisition_for_another_context_or_phase_is_rejected(self):
        acquired = self.acquire()
        other = self.create(OTHER)
        with self.assertRaises(ObservationConflict):
            self.engine.prepare(other["id"], "wrong-context", 1, acquired, self.now)
        with self.assertRaises(ObservationConflict):
            self.engine.prepare(self.watch["id"], "wrong-time", 1, acquired, self.now - 1)

    def test_reused_snapshot_is_immutable_and_must_match_the_requested_watch(self):
        snapshot = self.engine.snapshot(self.watch["id"])
        with self.assertRaises(TypeError):
            snapshot.plan_ref["sha256"] = "0" * 64
        with self.assertRaises(ObservationConflict):
            replace(snapshot, plan=FrozenJSON.capture({"forged": True}))
        with self.assertRaises(ObservationConflict):
            self.engine.prepare(
                self.create()["id"], "wrong-snapshot", 1, {}, self.now, snapshot=snapshot
            )

    def test_reused_snapshot_avoids_a_second_read_but_is_still_checked_at_commit(self):
        snapshot = self.engine.snapshot(self.watch["id"])
        acquired = self.acquire()
        with patch.object(
            self.engine.repository,
            "snapshot",
            side_effect=AssertionError("redundant snapshot read"),
        ):
            prepared = self.engine.prepare(
                self.watch["id"], "reused", 1, acquired, self.now, snapshot=snapshot
            )
        with self.engine.connect() as db:
            db.execute("UPDATE watches SET phase='after' WHERE id=?", (self.watch["id"],))
        with self.assertRaises(ObservationConflict):
            self.engine.commit(prepared)

    def test_prepared_metadata_cannot_be_redirected_to_another_watch(self):
        prepared = self.prepared()
        with self.assertRaises(ObservationConflict):
            replace(prepared, watch_id=self.create()["id"])

    def test_serialized_preparation_is_not_an_accepted_internal_handoff(self):
        with self.assertRaises(TypeError):
            self.engine.commit({"result": {"status": "ready"}})
        result = self.engine.submit(
            self.watch["id"], "forged", 1, {"result": {"status": "ready"}}, self.now
        )
        self.assertEqual(result["receipt"]["result"]["status"], "unknown")

    def test_plan_context_or_phase_change_between_prepare_and_commit_is_fenced(self):
        prepared = self.prepared()
        changed = dict(self.watch["task"], context={"component_id": OTHER})
        ref = self.store.put(changed)
        with self.engine.connect() as db:
            db.execute(
                "UPDATE watches SET task_ref=? WHERE id=?", (json.dumps(ref), self.watch["id"])
            )
        with self.assertRaises(ObservationConflict):
            self.engine.commit(prepared)
        self.assertEqual(self.engine.usage()["observations"], 0)

    def test_newer_observation_wins_over_an_older_prepared_decision(self):
        old = self.prepared()
        envelope = deepcopy(self.good)
        envelope["body"]["components"][0]["status"] = "major_outage"
        with (
            patch("ternela.connectors.read_json", return_value=envelope),
            patch("ternela.acquisition.time.time", return_value=self.now),
        ):
            newer = acquisition.acquire(
                self.watch["plan"], self.watch["task"]["context"], "before", "github_status"
            )
        self.engine.submit(self.watch["id"], "newer", 2, newer, self.now)
        self.assertFalse(self.engine.commit(old)["accepted"])
        self.assertEqual(
            self.engine.status(self.watch["id"])["receipt"]["result"]["status"], "blocked"
        )

    def test_prepared_evidence_expiring_before_commit_is_unknown(self):
        prepared = self.prepared()
        self.now += 121
        result = self.engine.commit(prepared)
        self.assertTrue(result["receipt"]["result"]["stale"])
        self.assertEqual(result["receipt"]["result"]["status"], "unknown")

    def test_expiry_during_blob_publication_cannot_publish_fresh_success(self):
        prepared = self.prepared()
        original = self.store.put

        def slow(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            if isinstance(value, dict) and "observations" in value:
                self.now = 1121.0
            return result

        with patch.object(self.store, "put", side_effect=slow):
            result = self.engine.commit(prepared)
        self.assertEqual(result["receipt"]["result"]["status"], "unknown")
        self.assertEqual(self.engine.usage()["unknown_observations"], 1)

    def test_pause_between_prepare_and_commit_rejects_a_monitor_receipt(self):
        runner = monitor.Monitors(self.engine)
        runner.start(self.watch["id"], 60)
        claim = runner.claim()
        prepared = self.prepared()
        runner.pause(self.watch["id"])
        with self.assertRaises(LeaseExpired):
            self.engine.commit(prepared, fence=LeaseFence(self.watch["id"], claim["lease_token"]))
        self.assertEqual(self.engine.usage()["observations"], 0)

    def test_lease_expiring_during_blob_write_rolls_back_receipt_outbox_and_usage(self):
        runner = monitor.Monitors(self.engine)
        runner.start(self.watch["id"], 60)
        claim = runner.claim()
        prepared = self.prepared()
        original = self.store.put

        def slow(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            self.now += 91
            return result

        with patch.object(self.store, "put", side_effect=slow), self.assertRaises(LeaseExpired):
            self.engine.commit(prepared, fence=LeaseFence(self.watch["id"], claim["lease_token"]))
        self.assertEqual(self.engine.get(self.watch["id"])["revision"], 0)
        self.assertEqual(self.engine.outbox(self.watch["id"]), [])
        self.assertEqual(self.engine.usage()["observations"], 0)

    def test_deadline_expiring_during_publication_is_also_fenced(self):
        runner = monitor.Monitors(self.engine)
        runner.start(self.watch["id"], 60, duration=60)
        claim = runner.claim()
        prepared = self.prepared()
        original = self.store.put

        def slow(value, *args, **kwargs):
            result = original(value, *args, **kwargs)
            self.now += 61
            return result

        with patch.object(self.store, "put", side_effect=slow), self.assertRaises(LeaseExpired):
            self.engine.commit(prepared, fence=LeaseFence(self.watch["id"], claim["lease_token"]))

    def test_foreign_fence_and_clock_rollback_are_rejected(self):
        prepared = self.prepared()
        with self.assertRaises(ObservationConflict):
            self.engine.commit(prepared, fence=LeaseFence(self.create()["id"], "token"))
        self.now -= 10
        with self.assertRaises(ObservationConflict):
            self.engine.commit(prepared)

    def test_prepared_retries_preserve_event_deduplication_and_typed_conflicts(self):
        prepared = self.prepared()
        self.engine.commit(prepared)
        self.assertTrue(self.engine.commit(prepared)["duplicate"])
        self.assertEqual(self.engine.usage()["observations"], 1)
        changed = self.engine.prepare(self.watch["id"], "1", 2, {}, self.now)
        with self.assertRaises(EventConflict):
            self.engine.commit(changed)

    def test_concurrent_prepared_commits_cannot_undo_a_newer_revision(self):
        prepared = [self.prepared(n) for n in range(1, 13)]
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(self.engine.commit, prepared))
        self.assertEqual(self.engine.get(self.watch["id"])["revision"], 12)

    def test_versioned_refs_keep_payload_hashes_and_legacy_access_compatible(self):
        self.engine.commit(self.prepared())
        with self.engine.connect() as db:
            row = db.execute("SELECT plan_ref,receipt_ref FROM watches").fetchone()
        for key, kind in (("plan_ref", "plan"), ("receipt_ref", "receipt")):
            ref = json.loads(row[key])
            self.assertEqual((ref["record_type"], ref["record_version"]), (kind, 1))
            legacy = {k: v for k, v in ref.items() if not k.startswith("record_")}
            self.assertEqual(
                read_record(self.store, ref, kind), read_record(self.store, legacy, kind)
            )
            self.assertEqual(self.store.get(ref), self.store.get(legacy))

    def test_unknown_or_incomplete_record_versions_fail_before_loading_payload(self):
        ref = write_record(self.store, {"example": True}, "plan")
        for changed in (
            dict(ref, record_version=99),
            dict(ref, record_version=True),
            dict(ref, record_type="receipt"),
            {k: v for k, v in ref.items() if k != "record_version"},
        ):
            with (
                self.subTest(ref=changed),
                patch.object(
                    self.store,
                    "get",
                    side_effect=AssertionError("payload read before version check"),
                ),
                self.assertRaises(UnsupportedVersion),
            ):
                read_record(self.store, changed, "plan")

    def test_new_record_version_between_prepare_and_commit_is_not_interpreted(self):
        prepared = self.prepared()
        with self.engine.connect() as db:
            ref = json.loads(db.execute("SELECT plan_ref FROM watches").fetchone()[0])
            ref["record_version"] = 99
            db.execute("UPDATE watches SET plan_ref=?", (json.dumps(ref),))
        with self.assertRaises(UnsupportedVersion):
            self.engine.commit(prepared)
        with self.assertRaises(UnsupportedVersion):
            self.engine.directory()

    def test_monitor_uses_repositories_and_evaluates_once(self):
        runner = monitor.Monitors(self.engine)
        with patch.object(self.engine, "connect", side_effect=AssertionError("monitor leaked SQL")):
            runner.start(self.watch["id"], 60)
            with (
                patch("ternela.connectors.read_json", return_value=deepcopy(self.good)),
                patch("ternela.acquisition.time.time", return_value=self.now),
                patch("ternela.observations.evaluate", wraps=observations.evaluate) as evaluate,
            ):
                runner.run_once()
            runner.pause(self.watch["id"])
        self.assertEqual(evaluate.call_count, 1)

    def test_blob_publication_retries_a_missing_read_once_without_hiding_corruption(self):
        value = {"published": True}
        from ternela.storage import canonical, sha

        expected = (
            self.store.root / "blobs" / sha(canonical(value))[:2] / (sha(canonical(value)) + ".gz")
        )
        original = Path.read_bytes
        lost = [False]

        def reader(path):
            if path == expected and not lost[0]:
                lost[0] = True
                raise FileNotFoundError("transient visibility")
            return original(path)

        with patch.object(Path, "read_bytes", reader):
            ref = self.store.put(value)
        self.assertTrue(lost[0])
        self.assertEqual(self.store.get(ref), value)
        expected.write_bytes(gzip.compress(b"{}"))
        with self.assertRaisesRegex(ValueError, "Corrupt content-addressed blob"):
            self.store.put(value)
        with (
            patch.object(Path, "read_bytes", side_effect=FileNotFoundError("permanently absent")),
            self.assertRaises(FileNotFoundError),
        ):
            self.store.put({"never": "visible"})

    def test_recipe_argument_validation_does_not_swallow_a_builder_bug(self):
        from ternela.connectors import recipe_plan
        from ternela.providers.github_status import GithubStatusConnector

        class Broken(GithubStatusConnector):
            def recipe(self, component_id):
                raise TypeError("implementation bug")

        base = CONNECTORS.get("github_status")
        broken = Broken(base.provider, base.host, base.operation, base.documentation)
        with patch("ternela.connectors.CONNECTORS", ConnectorRegistry((broken,))):
            with self.assertRaises(RecipeArgumentsError):
                recipe_plan("github_status", {})
            with self.assertRaisesRegex(TypeError, "implementation bug"):
                recipe_plan("github_status", {"component_id": TARGET})


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.path = root / "state.sqlite3"
        self.store = Store(root / "data")
        self.clock = lambda: 1000.0

    def inspect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        return db

    def test_new_database_has_ordered_history_and_restart_does_not_reapply(self):
        database = Database(self.path, self.store, self.clock)
        with database.connect() as db:
            first = [
                tuple(row) for row in db.execute("SELECT * FROM schema_migrations ORDER BY version")
            ]
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        database = Database(self.path, self.store, lambda: 2000.0)
        with database.connect() as db:
            self.assertEqual(
                [
                    tuple(row)
                    for row in db.execute("SELECT * FROM schema_migrations ORDER BY version")
                ],
                first,
            )

    def test_newer_database_is_rejected_without_changing_it(self):
        db = self.inspect()
        db.execute("PRAGMA user_version=999")
        db.execute("CREATE TABLE sentinel(value TEXT)")
        db.execute("INSERT INTO sentinel VALUES('keep')")
        db.commit()
        before = self.path.read_bytes()
        with self.assertRaises(UnsupportedVersion):
            Database(self.path, self.store, self.clock)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(db.execute("SELECT value FROM sentinel").fetchone()[0], "keep")

    def test_migration_failure_rolls_back_all_new_ddl_and_version(self):
        db = self.inspect()

        def fail(connection, store):
            connection.execute("CREATE TABLE partial_write(value TEXT)")
            raise OSError("disk full")

        with self.assertRaises(OSError), db:
            migrate(db, self.store, self.clock, (Migration(1, "failing step", fail),))
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
        self.assertEqual(
            db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), []
        )
        Database(self.path, self.store, self.clock)

    def test_existing_v1_migrates_forward_without_rewriting_rows(self):
        db = self.inspect()
        with db:
            migrate(db, self.store, self.clock, MIGRATIONS[:1])
            db.execute(
                "INSERT INTO watches(id,phase,ttl,created,revision) VALUES('old','after',30,1000,7)"
            )
        database = Database(self.path, self.store, self.clock)
        with database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT revision FROM watches WHERE id='old'").fetchone()[0], 7
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
            )

    def wal_fault(self, code, once=False):
        original = sqlite3.connect
        attempts = []

        class Connection(sqlite3.Connection):
            def execute(connection, sql, *args, **kwargs):
                if sql == "PRAGMA journal_mode=WAL":
                    attempts.append(sql)
                    if not once or len(attempts) == 1:
                        error = sqlite3.OperationalError("injected SQLite failure")
                        error.sqlite_errorcode = code
                        raise error
                return super().execute(sql, *args, **kwargs)

        def connect(*args, **kwargs):
            return original(*args, factory=Connection, **kwargs)

        return patch("ternela.database.sqlite3.connect", side_effect=connect), attempts

    def test_wal_startup_retries_transient_busy(self):
        fault, attempts = self.wal_fault(sqlite3.SQLITE_BUSY, once=True)
        with fault:
            database = Database(self.path, self.store, self.clock)
        with database.connect() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(len(attempts), 2)

    def test_wal_startup_does_not_retry_nonlocking_errors(self):
        fault, attempts = self.wal_fault(sqlite3.SQLITE_IOERR)
        with fault, patch("ternela.database.time.sleep") as sleep:
            with self.assertRaises(sqlite3.OperationalError):
                Database(self.path, self.store, self.clock)
        self.assertEqual(len(attempts), 1)
        sleep.assert_not_called()

    def test_wal_startup_busy_wait_is_bounded(self):
        fault, attempts = self.wal_fault(sqlite3.SQLITE_BUSY)
        with fault, patch("ternela.database.time.monotonic", side_effect=[0, 16]):
            with self.assertRaises(sqlite3.OperationalError):
                Database(self.path, self.store, self.clock)
        self.assertEqual(len(attempts), 1)

    def test_concurrent_initializers_apply_each_migration_once(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda _: Database(self.path, self.store, self.clock), range(4)))
        db = self.inspect()
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], SCHEMA_VERSION
        )

    def test_corrupt_history_is_rejected_and_not_repaired_silently(self):
        Database(self.path, self.store, self.clock)
        db = self.inspect()
        db.execute("UPDATE schema_migrations SET name='tampered' WHERE version=1")
        db.commit()
        with self.assertRaises(UnsupportedVersion):
            Database(self.path, self.store, self.clock)
        self.assertEqual(
            db.execute("SELECT name FROM schema_migrations WHERE version=1").fetchone()[0],
            "tampered",
        )

    def test_nonconsecutive_migration_inventory_is_rejected(self):
        db = self.inspect()
        with self.assertRaises(ValueError):
            migrate(db, self.store, self.clock, (Migration(2, "gap", lambda *args: None),))
