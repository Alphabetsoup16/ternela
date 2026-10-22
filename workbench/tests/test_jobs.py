# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path

from expansion_lab.storage import Store, write_json
from workbench.server import Service


class RecoveredJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.service = Service.__new__(Service)
        self.service.jobs = {}
        self.service.store = Store(self.root / "data")
        self.identity = "task-" + "a" * 32
        self.task = {"id": self.identity, "task": "Observe this exact release"}
        write_json(self.root / "data/tasks" / self.identity / "public.json", {"tasks": [self.task]})
        self.manifest = self.root / "data/runs" / self.identity / "manifest.json"

    def test_only_completed_admitted_plan_is_recovered(self):
        plan = {"task_id": self.identity}
        write_json(
            self.manifest,
            {
                "status": "completed",
                "admission_status": "accepted",
                "plans": self.service.store.put({"plans": [plan]}),
            },
        )
        self.assertEqual(self.service.job(self.identity)["plan"], plan)

    def test_unfinished_job_is_reported_as_interrupted(self):
        self.assertEqual(self.service.job(self.identity)["status"], "interrupted")

    def test_failed_admission_is_not_recovered_as_complete_or_interrupted(self):
        write_json(self.manifest, {"status": "failed", "admission_status": "rejected"})
        self.assertEqual(self.service.job(self.identity)["status"], "failed")

    def test_invalid_job_path_does_not_read_arbitrary_files(self):
        for identity in ("../public.json", "task-" + "z" * 32, "task-" + "a" * 32 + "/extra"):
            with self.assertRaises(KeyError):
                self.service.job(identity)
