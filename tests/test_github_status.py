# SPDX-License-Identifier: Apache-2.0
"""Pre-implementation status-page expectations; do not tune labels to the adapter."""

import gzip
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import PROJECT, Store, canonical, sha
from ternela.connectors import CONNECTORS
from ternela.validation import admit
from workbench.engine import Watches, observe
from workbench.live import collect
from workbench.monitor import Monitors

PROVIDER = "github_status"
TARGET = "br0l2tvcx85d"
OTHER = "brv1bkgrwx7q"


def corpus():
    manifest = json.loads((PROJECT / "evals/github_status_v1.manifest.json").read_text())
    packed = (PROJECT / "evals/github_status_v1.json.gz").read_bytes()
    raw = gzip.decompress(packed)
    if sha(raw) != manifest["raw_sha256"] or sha(packed) != manifest["gzip_sha256"]:
        raise ValueError("Frozen status corpus hash mismatch")
    value = json.loads(raw)
    assert len(value["cases"]) == manifest["cases"]
    return value, manifest


def acquisition(connector, case):
    task, plan = connector.recipe(**case["context"])
    with patch("ternela.connectors.read_json", return_value=deepcopy(case["envelope"])) as reader:
        snapshots, _ = collect(plan, task["context"], "before", PROVIDER)
    result, observed = observe(plan, task["context"], snapshots, "before")
    return task, plan, result, observed, snapshots, reader


class GithubStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connector = CONNECTORS.get(PROVIDER)
        cls.corpus, cls.manifest = corpus()
        cls.good = cls.corpus["cases"][0]

    def test_frozen_expectations_are_unchanged(self):
        self.assertFalse(self.manifest["adapter_existed_at_freeze"])
        self.assertEqual(len(self.corpus["cases"]), 55)
        self.assertEqual(len({case["id"] for case in self.corpus["cases"]}), 55)

    def test_all_frozen_cases_use_the_same_admitted_plan(self):
        plan_hashes = set()
        for case in self.corpus["cases"]:
            with self.subTest(case=case["id"]):
                task, plan, result, _, snapshots, reader = acquisition(self.connector, case)
                admit(plan, task)
                plan_hashes.add(sha(canonical(plan)))
                self.assertEqual(result["status"], case["expected"])
                reader.assert_called_once()
                self.assertEqual(
                    reader.call_args.args[:2],
                    ("https://www.githubstatus.com/api/v2/summary.json", "www.githubstatus.com"),
                )
                if 200 <= case["envelope"]["http_status"] < 300:
                    self.assertEqual(
                        snapshots["summary"][0]["source_body"], case["envelope"]["body"]
                    )
        self.assertEqual(len(plan_hashes), 1)

    def test_required_single_selection_preserves_legacy_empty_semantics(self):
        from docs_lab.v2.runtime import check_one

        _, plan = self.connector.recipe(component_id=TARGET)
        check = deepcopy(next(c for c in plan["checks"] if c["id"] == "status"))
        envelope = {
            "http_status": 200,
            "body": {"components": []},
            "collection_complete": True,
            "completed_pointer": "/components",
        }
        context = {"component_id": TARGET}
        self.assertEqual(check_one(check, envelope, context), "unknown")
        legacy = dict(check, select_mode="single")
        self.assertEqual(check_one(legacy, envelope, context), "false")
        row = {"id": TARGET, "status": "operational"}
        envelope["body"]["components"] = [row, row]
        self.assertEqual(check_one(check, envelope, context), "unknown")
        envelope["body"]["components"] = [row]
        self.assertEqual(check_one(check, envelope, context), "true")
        envelope["body"]["components"][0]["status"] = "partial_outage"
        self.assertEqual(check_one(check, envelope, context), "false")

    def test_other_component_is_selected_by_identity(self):
        case = deepcopy(self.good)
        case["context"]["component_id"] = OTHER
        case["envelope"]["body"]["components"][0]["status"] = "major_outage"
        self.assertEqual(acquisition(self.connector, case)[2]["status"], "ready")

    def test_bad_component_arguments_fail_without_a_read(self):
        with patch("ternela.connectors.read_json") as reader:
            for value in (
                None,
                True,
                [],
                "Actions",
                "",
                "../anything",
                TARGET + "/x",
                "https://evil.example",
            ):
                with self.assertRaises(ValueError):
                    self.connector.recipe(component_id=value)
            reader.assert_not_called()

    def test_default_monitor_survives_outage_restart_and_expiry(self):
        now = 1000.0
        envelope = deepcopy(self.good["envelope"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Watches(root / "state.sqlite3", Store(root / "data"), lambda: now)
            task, plan = self.connector.recipe(component_id=TARGET)
            watch = engine.create(task, plan, phase="before", ttl=120)
            monitors = Monitors(engine)
            self.assertTrue(monitors.start(watch["id"], interval=60)["enabled"])
            with (
                patch("ternela.connectors.read_json", side_effect=lambda *args: deepcopy(envelope)),
                patch("workbench.live.time.time", side_effect=lambda: now),
            ):
                monitors.run_once()
                self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "ready")
                now += 60
                envelope["body"]["components"][0]["status"] = "degraded_performance"
                monitors.run_once()
                self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "blocked")
                now += 60
                envelope["http_status"] = 503
                monitors.run_once()
                self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "unknown")
                now += 120
                envelope["http_status"] = 200
                envelope["body"]["components"][0]["status"] = "operational"
                restarted = Watches(engine.path, engine.store, lambda: now)
                Monitors(restarted).run_once()
                self.assertEqual(restarted.get(watch["id"])["receipt"]["result"]["status"], "ready")
            now += 121
            self.assertTrue(restarted.get(watch["id"])["receipt"]["result"]["stale"])
            messages = restarted.outbox(watch["id"])
            self.assertEqual(
                [m["receipt"]["result"]["status"] for m in messages],
                ["ready", "blocked", "unknown", "unknown", "ready", "unknown"],
            )
            restarted.ack(watch["id"], messages[-1]["id"])
            again = Watches(engine.path, engine.store, lambda: now)
            self.assertIsNotNone(again.outbox(watch["id"])[-1]["acked"])

    def test_pause_fences_a_status_read_in_flight(self):
        now = 1000.0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Watches(root / "state.sqlite3", Store(root / "data"), lambda: now)
            task, plan = self.connector.recipe(component_id=TARGET)
            watch = engine.create(task, plan, phase="before", ttl=120)
            monitors = Monitors(engine)
            monitors.start(watch["id"], interval=60)

            def paused(*args):
                monitors.pause(watch["id"])
                return deepcopy(self.good["envelope"])

            with (
                patch("ternela.connectors.read_json", side_effect=paused),
                patch("workbench.live.time.time", return_value=now),
            ):
                self.assertTrue(monitors.run_once())
            self.assertEqual(engine.get(watch["id"])["revision"], 0)
            self.assertEqual(engine.outbox(watch["id"]), [])

    def test_late_revision_cannot_restore_ready_over_a_blocked_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = Watches(root / "state.sqlite3", Store(root / "data"))
            task, plan, _, _, ready, _ = acquisition(self.connector, self.good)
            watch = engine.create(task, plan, phase="before", ttl=120)
            bad = deepcopy(self.good)
            bad["envelope"]["body"]["components"][0]["status"] = "major_outage"
            blocked = acquisition(self.connector, bad)[4]
            engine.submit(watch["id"], "blocked", 2, blocked)
            delivered = engine.submit(watch["id"], "late-ready", 1, ready)
            self.assertFalse(delivered["accepted"])
            self.assertEqual(engine.get(watch["id"])["receipt"]["result"]["status"], "blocked")
