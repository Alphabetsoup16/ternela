# SPDX-License-Identifier: Apache-2.0
"""Compiler migration: subprocess orchestration, failures, reuse and legacy seams."""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.compiler_support import Fixture
from ternela import plan_schema
from ternela.check import boundary_issues
from ternela.compiler import inference, jobs, run, schema
from ternela.storage import PROJECT


class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="compiler-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = Fixture(self.root)

    def execute(self, module="ternela.compiler"):
        return self.fixture.run(module, cwd=PROJECT)

    def infer(
        self,
        *,
        instructions="Compile only supplied evidence",
        payload=None,
        stage="compile",
        reuse=None,
    ):
        manifest = dict(stages={}, reuse_from=reuse)
        path = self.fixture.data / "runs/current/manifest.json"
        self.manifest = manifest
        with patch.dict(os.environ, self.fixture.env, clear=True):
            result = inference.infer(
                payload or {"tasks": [self.fixture.task]},
                stage,
                schema.BATCH,
                instructions,
                path,
                manifest,
                self.fixture.store,
            )
        return result

    def configure(self, **updates):
        config = json.loads(self.fixture.config.read_text())
        config.update(updates)
        self.fixture.config.write_text(json.dumps(config))

    def test_pipeline_runs_discovery_admission_and_persists_bounded_evidence(self):
        completed = self.execute()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        manifest = self.fixture.manifest()
        self.assertEqual(
            (manifest["status"], manifest["admission_status"]), ("completed", "accepted")
        )
        self.assertEqual(self.fixture.store.get(manifest["plans"]), self.fixture.batch)
        self.assertTrue({"discovery", "compile-0"} <= set(manifest["stages"]))
        self.assertLessEqual(len(manifest["stages"]), 5)
        self.assertLessEqual(len(manifest["expansions"]), 2)
        self.assertTrue(all(p.startswith("ternela/") for p in manifest["code_sha256"]))
        for call in self.fixture.calls():
            self.assertNotIn("PRIVATE-ORACLE", call["prompt"])
            for flag in ["--ignore-user-config", "--ephemeral", "--output-schema", "--json"]:
                self.assertIn(flag, call["argv"])
            self.assertIn("features.shell_tool=false", call["argv"])
            self.assertIn('web_search="disabled"', call["argv"])
            self.assertIn("apps._default.enabled=false", call["argv"])

    def test_service_launches_new_compiler_and_recovers_the_saved_job(self):
        from concurrent.futures import ThreadPoolExecutor

        from workbench.server import Service

        service = Service.__new__(Service)
        service.store = self.fixture.store
        service.jobs = {}
        with patch.dict(os.environ, self.fixture.env, clear=True):
            with ThreadPoolExecutor(max_workers=1) as pool:
                service.pool = pool
                job = service.compile(self.fixture.task)
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(job["plan"]["task_id"], job["id"])
        self.assertTrue(self.fixture.calls())
        service.jobs = {}
        recovered = service.job(job["id"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["plan"], job["plan"])

    def test_discovery_outside_supplied_catalog_fails_before_plan_inference(self):
        self.configure(mode="outside")
        completed = self.execute()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("outside the supplied evidence", completed.stderr)
        self.assertEqual(len(self.fixture.calls()), 1)
        self.assertEqual(self.fixture.manifest()["status"], "failed")

    def test_public_feedback_repairs_an_invalid_plan_before_admission(self):
        self.configure(mode="repair")
        completed = self.execute()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        manifest = self.fixture.manifest()
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(self.fixture.store.get(manifest["plans"]), self.fixture.batch)
        expansion = self.fixture.store.get(manifest["expansions"][0])
        self.assertTrue(
            any(
                "executable checks" in issue
                for issue in expansion["public_validation_feedback"][self.fixture.job_id]
            )
        )
        self.assertIn("compile-1", manifest["stages"])

    def test_exhausted_repair_is_saved_as_rejected_and_fallback_is_bounded(self):
        self.configure(mode="admission")
        completed = self.execute()
        self.assertNotEqual(completed.returncode, 0)
        manifest = self.fixture.manifest()
        self.assertEqual((manifest["status"], manifest["admission_status"]), ("failed", "rejected"))
        self.assertEqual(len(manifest["expansions"]), 2)
        events = [
            event
            for ref in manifest["expansions"]
            for event in self.fixture.store.get(ref)["events"]
        ]
        self.assertEqual(sum(e["kind"] == "complete_directory_fallback" for e in events), 1)
        self.assertLessEqual(len(self.fixture.calls()), 5)
        self.assertTrue(manifest["plans"])

    def test_compiler_imports_and_source_inventory_need_no_lab_or_interface(self):
        code = "from ternela.compiler import jobs,run; from ternela.compiler.guides import load; import sys; assert run.code_manifest() and load(); assert not [n for n in sys.modules if n.split('.')[0].endswith('_lab') or n.split('.')[0]=='workbench']"
        subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=PROJECT,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_moved_modules_preserve_identity_and_mock_seams(self):
        for old, new in [
            ("workbench.compiler", "jobs"),
            ("expansion_lab.run", "run"),
            ("expansion_lab.inference", "inference"),
            ("expansion_lab.evidence", "evidence"),
            ("safety_lab.run", "feedback"),
            ("docs_lab.v2.guides", "guides"),
            ("docs_lab.v2.retrieval", "retrieval"),
        ]:
            self.assertIs(
                importlib.import_module(old), importlib.import_module("ternela.compiler." + new)
            )
        self.assertIs(schema.PLAN, plan_schema.PLAN)
        self.assertIs(schema.BATCH, plan_schema.BATCH)
        old = importlib.import_module("expansion_lab.run")
        with patch.object(old, "infer", return_value="shared seam"):
            self.assertEqual(run.infer(), "shared seam")

    def test_legacy_workbench_command_still_compiles(self):
        completed = self.execute("workbench.compiler")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(self.fixture.manifest()["status"], "completed")

    def test_existing_run_is_never_overwritten(self):
        path = self.fixture.data / "runs" / self.fixture.job_id / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"sentinel":"keep"}')
        completed = self.execute()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(path.read_text(), '{"sentinel":"keep"}')
        self.assertEqual(self.fixture.calls(), [])

    def test_changed_frozen_fixture_fails_before_inference(self):
        path = self.fixture.data / "tasks" / self.fixture.job_id / "private.json"
        path.write_text("{}")
        completed = self.execute()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Frozen fixture changed", completed.stderr)
        self.assertEqual(self.fixture.calls(), [])

    def test_job_identity_is_rejected_before_work(self):
        for job in ["../escape", "task-" + "z" * 32, "task-" + "a" * 31]:
            with self.assertRaises(SystemExit):
                jobs.main([job])
        self.assertEqual(self.fixture.calls(), [])

    def test_operational_failure_retries_once_and_retains_both_attempts(self):
        self.configure(operational_failures=1)
        self.assertEqual(self.infer(), self.fixture.batch)
        attempts = self.manifest["stages"]["compile"]["attempts"]
        self.assertEqual([a["status"] for a in attempts], ["failed", "completed"])
        self.assertEqual(attempts[0]["prompt_sha256"], attempts[1]["prompt_sha256"])
        self.assertEqual(len(self.fixture.calls()), 2)

    def test_exhausted_operational_failure_is_bounded_and_saved(self):
        self.configure(operational_failures=9)
        with self.assertRaises(RuntimeError):
            self.infer()
        stage = self.manifest["stages"]["compile"]
        self.assertEqual(stage["status"], "failed")
        self.assertEqual(len(stage["attempts"]), 2)
        self.assertTrue(all(a["stderr"]["sha256"] for a in stage["attempts"]))

    def test_timeout_retains_trace_and_attempt_bound(self):
        with patch.object(
            inference.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 240)
        ) as execute:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.infer()
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(self.manifest["stages"]["compile"]["status"], "failed")
        self.assertTrue(all(c.kwargs["timeout"] == 240 for c in execute.call_args_list))

    def test_completed_shape_or_identity_errors_are_not_retried(self):
        for mode in ["shape", "duplicate", "identity"]:
            with self.subTest(mode=mode):
                self.configure(mode=mode)
                before = len(self.fixture.calls())
                with self.assertRaises(ValueError):
                    self.infer()
                self.assertEqual(len(self.fixture.calls()) - before, 1)
                self.assertEqual(
                    self.manifest["stages"]["compile"]["status"], "invalid_completed_output"
                )

    def test_reported_tool_attempt_cannot_produce_completed_output(self):
        self.configure(mode="tool")
        with self.assertRaisesRegex(RuntimeError, "attempted tools"):
            self.infer()
        self.assertEqual(self.manifest["stages"]["compile"]["status"], "failed")
        self.assertEqual(len(self.fixture.calls()), 2)

    def test_oversized_input_is_rejected_before_cli_or_stage_writes(self):
        with self.assertRaisesRegex(ValueError, "context budget"):
            self.infer(instructions="x" * 950001)
        self.assertEqual(self.fixture.calls(), [])
        self.assertEqual(self.manifest["stages"], {})

    def test_identical_completed_stage_reuses_exact_prompt_without_cli(self):
        payload = {"tasks": [self.fixture.task], "alpha": 1, "beta": 2}
        self.infer(payload=payload)
        previous = deepcopy(self.manifest)
        from ternela.storage import write_json

        write_json(self.fixture.data / "runs/previous/manifest.json", previous)
        count = len(self.fixture.calls())
        reordered = {"beta": 2, "alpha": 1, "tasks": [self.fixture.task]}
        self.assertEqual(self.infer(payload=reordered, reuse="previous"), self.fixture.batch)
        self.assertEqual(len(self.fixture.calls()), count)
        self.assertEqual(self.manifest["stages"]["compile"]["reused_from"], "previous")
        self.assertEqual(
            self.manifest["stages"]["compile"]["prompt"], previous["stages"]["compile"]["prompt"]
        )

    def test_changed_instructions_payload_or_schema_cannot_reuse(self):
        self.infer()
        from ternela.storage import write_json

        write_json(self.fixture.data / "runs/previous/manifest.json", self.manifest)
        for options in [
            dict(instructions="Changed policy"),
            dict(payload={"tasks": [self.fixture.task], "new_evidence": True}),
        ]:
            before = len(self.fixture.calls())
            self.infer(reuse="previous", **options)
            self.assertEqual(len(self.fixture.calls()) - before, 1)
        changed = deepcopy(schema.BATCH)
        changed["properties"]["plans"]["maxItems"] = 1
        with patch.object(schema, "BATCH", changed):
            before = len(self.fixture.calls())
            self.infer(reuse="previous")
            self.assertEqual(len(self.fixture.calls()) - before, 1)

    def test_new_transitive_compiler_lab_dependency_is_rejected(self):
        root = self.root / "policy"
        directory = root / "ternela/compiler"
        directory.mkdir(parents=True)
        (directory / "jobs.py").write_text("from .helper import value\n")
        (directory / "helper.py").write_text("from expansion_lab.runtime import run_plan\n")
        self.assertTrue(any("stable runtime" in item for item in boundary_issues(root)))


if __name__ == "__main__":
    unittest.main()
