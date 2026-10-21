# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import Store
from source_lab.evidence import SourceIndex
from workbench.demo import catalog, snapshots
from workbench.engine import Watches
from workbench.monitor import Monitors


class MonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.templates = {entry["task"]["id"]: entry for entry in catalog()}
        cls.indexes = {provider: SourceIndex(provider) for provider in ("github", "stripe")}

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.now = 1000.0
        self.store = Store(root / "data")
        self.engine = Watches(root / "watches.sqlite3", self.store, lambda: self.now)
        self.template = self.templates["n04"]
        self.watch = self.create(self.template)
        self.case = "success"
        self.calls = 0
        self.monitors = Monitors(self.engine, self.collect)

    def create(self, entry):
        return self.engine.create(entry["task"], entry["plan"], entry["phase"], 900, self.indexes)

    def collect(self, plan, context, phase):
        self.calls += 1
        return snapshots(self.template["task"], plan, self.case, phase), self.now

    def current(self):
        return self.engine.get(self.watch["id"])

    def start(self):
        return self.monitors.start(self.watch["id"], 60)

    def test_schedule_survives_restart_and_respects_due_time(self):
        self.start()
        restarted = Monitors(Watches(self.engine.path, self.store, lambda: self.now), self.collect)
        self.assertTrue(restarted.run_once())
        self.assertEqual(self.current()["receipt"]["result"]["status"], "complete")
        self.assertFalse(restarted.run_once())
        self.now += 60
        self.assertTrue(restarted.run_once())
        self.assertEqual(self.calls, 2)
        self.assertEqual(len(self.engine.outbox(self.watch["id"])), 1)

    def test_only_one_concurrent_worker_claims_a_watch(self):
        self.start()
        with ThreadPoolExecutor(max_workers=4) as workers:
            claims = list(workers.map(lambda _: self.monitors.claim(), range(4)))
        self.assertEqual(sum(claim is not None for claim in claims), 1)

    def test_crash_before_read_recovers_after_lease_expiry(self):
        self.start()
        claim = self.monitors.claim()
        self.assertIsNotNone(claim)
        restarted = Monitors(self.engine, self.collect)
        self.assertFalse(restarted.run_once())
        self.now += 91
        self.assertTrue(restarted.run_once())
        self.assertEqual(self.current()["revision"], 1)

    def test_repeated_start_does_not_steal_lease(self):
        self.start()
        claim = self.monitors.claim()
        self.start()
        with self.engine.connect() as db:
            token = db.execute("SELECT lease_token FROM monitors").fetchone()[0]
        self.assertEqual(token, claim["lease_token"])
        self.assertNotIn("lease_token", self.monitors.get(self.watch["id"]))

    def test_pause_invalidates_in_flight_read(self):
        self.start()

        def paused(plan, context, phase):
            self.monitors.pause(self.watch["id"])
            return self.collect(plan, context, phase)

        self.monitors.collector = paused
        self.assertTrue(self.monitors.run_once())
        self.assertEqual(self.current()["revision"], 0)
        self.assertEqual(self.engine.outbox(self.watch["id"]), [])
        self.assertFalse(self.monitors.run_once())

    def test_configuration_change_fences_old_read(self):
        self.start()

        def changed(plan, context, phase):
            self.monitors.start(self.watch["id"], 120)
            return self.collect(plan, context, phase)

        self.monitors.collector = changed
        self.monitors.run_once()
        self.assertEqual(self.current()["revision"], 0)
        self.assertEqual(self.monitors.get(self.watch["id"])["interval"], 120)

    def test_expired_worker_cannot_commit(self):
        self.start()

        def slow(plan, context, phase):
            self.now += 91
            return self.collect(plan, context, phase)

        self.monitors.collector = slow
        self.monitors.run_once()
        self.assertEqual(self.current()["revision"], 0)
        self.monitors.collector = self.collect
        self.monitors.run_once()
        self.assertEqual(self.current()["revision"], 1)

    def test_outage_replaces_fresh_success_with_unknown_and_backs_off(self):
        self.start()
        self.monitors.run_once()
        self.now += 60
        self.case = "forbidden"
        self.monitors.run_once()
        self.assertEqual(self.current()["receipt"]["result"]["status"], "unknown")
        monitor = self.monitors.get(self.watch["id"])
        self.assertEqual(monitor["failures"], 1)
        self.assertEqual(monitor["due"], self.now + 120)
        self.assertEqual(monitor["last_success"], 1000)
        self.now += 120
        self.case = "success"
        self.monitors.run_once()
        self.assertEqual(self.monitors.get(self.watch["id"])["failures"], 0)

    def test_collector_exception_is_an_unknown_receipt(self):
        self.start()
        self.monitors.collector = lambda *args: (_ for _ in ()).throw(OSError("connection lost"))
        self.monitors.run_once()
        self.assertEqual(self.current()["receipt"]["result"]["status"], "unknown")
        self.assertEqual(self.monitors.get(self.watch["id"])["last_error"], "provider_unavailable")

    def test_newer_manual_update_wins_over_in_flight_monitor(self):
        self.start()

        def interleaved(plan, context, phase):
            body = snapshots(self.template["task"], plan, "queued", phase)
            self.engine.submit(self.watch["id"], "manual", 1, body, self.now)
            return self.collect(plan, context, phase)

        self.monitors.collector = interleaved
        self.monitors.run_once()
        self.assertEqual(self.current()["receipt"]["event_id"], "manual")
        self.assertEqual(self.current()["receipt"]["result"]["status"], "pending")
        self.assertEqual(
            self.monitors.get(self.watch["id"])["last_error"], "observation_superseded"
        )

    def test_crash_after_commit_does_not_duplicate_decision_change(self):
        self.start()
        original = self.engine.submit

        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("process stopped after committed receipt")

        with patch.object(self.engine, "submit", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.monitors.run_once()
        self.now += 91
        self.monitors.run_once()
        self.assertEqual(self.current()["revision"], 2)
        self.assertEqual(len(self.engine.outbox(self.watch["id"])), 1)

    def test_rate_limit_delay_applies_to_other_monitors_after_restart(self):
        self.start()

        def limited(plan, context, phase):
            body, at = self.collect(plan, context, phase)
            for page in body.values():
                page.update(http_status=429, headers={"retry-after": "600"})
            return body, at

        self.monitors.collector = limited
        self.monitors.run_once()
        second = self.create(self.template)
        self.monitors.start(second["id"], 60)
        restarted = Monitors(self.engine, self.collect)
        self.assertFalse(restarted.run_once())
        self.now += 600
        self.assertTrue(restarted.run_once())

    def test_rejects_invalid_intervals_and_unsupported_provider(self):
        for value in (True, "60", 59, 86401, 10**1000, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.monitors.start(self.watch["id"], value)
        payment = self.create(self.templates["n02"])
        with self.assertRaisesRegex(ValueError, "reviewed anonymous connector"):
            self.monitors.start(payment["id"])
        partial = deepcopy(self.template)
        partial["plan"]["requirements"][0]["coverage"] = "partial"
        watch = self.create(partial)
        with self.assertRaisesRegex(ValueError, "unverifiable requirements"):
            self.monitors.start(watch["id"])

    def test_explanation_distinguishes_missing_failed_and_expired_evidence(self):
        self.assertEqual(self.current()["explanation"]["reason"], "no_observation")
        self.start()
        self.case = "wrong_sha"
        self.monitors.run_once()
        explanation = self.current()["explanation"]
        self.assertEqual(explanation["status"], "pending")
        self.assertTrue(explanation["blockers"])
        self.now += 901
        self.assertEqual(self.current()["explanation"]["reason"], "expired")

    def test_deadline_stops_acquisition_and_retry_does_not_extend_it(self):
        self.monitors.start(self.watch["id"], 60, duration=120)
        self.now += 30
        self.monitors.start(self.watch["id"], 60, duration=120)
        self.assertEqual(self.monitors.get(self.watch["id"])["deadline"], 1120)
        self.now = 1120
        self.assertFalse(self.monitors.run_once())
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.monitors.get(self.watch["id"])["last_error"], "deadline_reached")
        self.monitors.start(self.watch["id"], 60, duration=120)
        self.assertTrue(self.monitors.run_once())

    def test_deadline_fences_a_read_that_started_before_it(self):
        self.monitors.start(self.watch["id"], 60, duration=60)

        def late(plan, context, phase):
            self.now += 61
            return self.collect(plan, context, phase)

        self.monitors.collector = late
        self.monitors.run_once()
        self.assertEqual(self.current()["revision"], 0)
