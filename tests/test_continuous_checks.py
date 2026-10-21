# SPDX-License-Identifier: Apache-2.0
"""Exercise the development loop, including faults that a green baseline must expose."""

import ast
import gzip
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from expansion_lab.storage import PROJECT, Store, canonical
from ternela import check
from ternela.connectors import CONNECTORS, ConnectorRegistry, release_plan
from ternela.providers.pypi import PyPIConnector
from ternela.replay import compare, example, harvest, load_corpus, replay, retain, shape
from workbench.demo import catalog, snapshots
from workbench.engine import Watches
from workbench.live import collect


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name, source in {
            "pyproject.toml": "[project]\ndependencies=[]\n",
            "ternela/__init__.py": "",
            "ternela/a.py": "VALUE=1\n",
            "ternela/b.py": "from . import a\n",
            "ternela/unused.py": "VALUE=2\n",
            "tests/test_a.py": "from ternela import b\n",
            "tests/test_other.py": "import unittest\n",
        }.items():
            self.write(name, source)
        self.before = check.snapshot(self.root)

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)

    def selection(self):
        return check.select_tests(self.root, check.snapshot(self.root), self.before)

    def run_cycle(self, runner=None):
        with redirect_stdout(io.StringIO()):
            return check.cycle(
                self.root,
                incremental=True,
                runner=runner
                or (
                    lambda root, selected: dict(
                        tests=len(selected), files=len(selected), success=True
                    )
                ),
            )

    def test_transitive_relative_imports_select_only_dependent_tests(self):
        self.write("ternela/a.py", "VALUE=3\n")
        self.assertEqual(self.selection(), (["tests/test_a.py"], "local import dependents"))

    def test_test_only_change_selects_that_test_and_unchanged_skips(self):
        self.assertEqual(self.selection()[0], [])
        self.write("tests/test_other.py", "import unittest\n# changed\n")
        self.assertEqual(self.selection()[0], ["tests/test_other.py"])

    def test_full_fallback_for_new_deleted_unmapped_fixture_and_dynamic_loading(self):
        for name, source in [
            ("ternela/new.py", "VALUE=1"),
            ("ternela/unused.py", "VALUE=3"),
            ("docs/example.json", "{}"),
            ("ternela/b.py", "__import__('ternela.a')"),
        ]:
            with self.subTest(name=name):
                self.write(name, source)
                self.assertEqual(self.selection()[0], check.test_files(self.root))
                self.before = check.snapshot(self.root)
        (self.root / "ternela/a.py").unlink()
        self.assertIn("deleted", self.selection()[1])

    def test_literal_mock_targets_are_included(self):
        self.write("tests/test_other.py", "patch('ternela.a.VALUE')\n")
        self.before = check.snapshot(self.root)
        self.write("ternela/a.py", "VALUE=8\n")
        self.assertEqual(self.selection()[0], check.test_files(self.root))

    def test_provider_runtime_and_network_imports_rejected_in_new_files(self):
        self.write(
            "ternela/providers/new.py",
            "from workbench import monitor\nfrom urllib import request\nimport threading\n",
        )
        self.assertEqual(len(check.boundary_issues(self.root)), 3)
        self.write("ternela/providers/new.py", "from urllib.parse import quote\n")
        self.assertEqual(check.boundary_issues(self.root), [])

    def test_unchanged_cycle_does_not_launch_test_processes(self):
        self.assertTrue(self.run_cycle()["success"])
        with patch(
            "ternela.check.subprocess.run", side_effect=AssertionError("unexpected process")
        ):
            self.assertTrue(self.run_cycle(check.run_tests)["success"])

    def test_failed_and_in_flight_changes_do_not_advance_baseline(self):
        self.run_cycle()
        baseline = self.root / "data/checks/baseline.json"
        original = baseline.read_bytes()
        self.write("ternela/a.py", "VALUE=4\n")
        failed = self.run_cycle(lambda *args: dict(tests=1, files=1, success=False))
        self.assertFalse(failed["success"])
        self.assertEqual(baseline.read_bytes(), original)

        def changing(*args):
            self.write("ternela/b.py", "from . import a\n# edited during run\n")
            return dict(tests=1, files=1, success=True)

        self.assertFalse(self.run_cycle(changing)["source_stable"])
        self.assertEqual(baseline.read_bytes(), original)

        def round_trip(*args):
            path = self.root / "ternela/a.py"
            original = path.read_bytes()
            path.write_text("VALUE=5\n")
            replacement = path.with_suffix(".tmp")
            replacement.write_bytes(original)
            replacement.replace(path)
            return dict(tests=1, files=1, success=True)

        self.assertFalse(self.run_cycle(round_trip)["source_stable"])
        self.assertEqual(baseline.read_bytes(), original)

    def test_dependency_change_reported_and_full_checked_without_approval(self):
        self.run_cycle()
        self.write("pyproject.toml", '[project]\ndependencies=["example==1.0"]\n')
        report = self.run_cycle()
        self.assertEqual(report["runtime_dependency_change"]["after"], ["example==1.0"])
        self.assertEqual(report["selected"], check.test_files(self.root))
        self.assertTrue(report["success"])

    def test_syntax_failure_is_visible_and_not_accepted(self):
        self.run_cycle()
        self.write("ternela/providers/broken.py", "def broken(:\n")
        report = self.run_cycle()
        self.assertFalse(report["success"])
        self.assertIn("SyntaxError", report["error"])

    def test_watch_is_idle_between_changes_and_drains_bounded_backlog(self):
        calls = []

        def run(*args, **kwargs):
            calls.append(1)
            return {"capture": {"pending": len(calls) == 1}}

        def sleep(_):
            if len(calls) == 2:
                self.write("ternela/a.py", "VALUE=9\n")

        check.watch_loop(self.root, self.root / "data", run=run, sleep=sleep, iterations=4)
        self.assertEqual(len(calls), 3)

    def test_watch_uses_fresh_child_by_default(self):
        with patch("ternela.check.fresh_cycle", return_value={}) as fresh:
            check.watch_loop(self.root, self.root / "data", sleep=lambda _: None, iterations=3)
        fresh.assert_called_once()

    def test_import_resolution_in_package_initializers(self):
        self.assertIn(
            "ternela.providers.pypi",
            check.imports(
                "ternela/providers/__init__.py", ast.parse("from .pypi import PyPIConnector")
            ),
        )

    def test_fresh_runner_rejects_same_size_edit_even_with_preserved_timestamp(self):
        import os
        import subprocess
        import sys

        self.write(
            "tests/test_actual.py",
            "import unittest\nfrom ternela.a import VALUE\nclass Actual(unittest.TestCase):\n def test_value(self): self.assertEqual(VALUE,1)\n",
        )
        subprocess.run([sys.executable, "-c", "import ternela.a"], cwd=self.root, check=True)
        path = self.root / "ternela/a.py"
        stat = path.stat()
        self.write("ternela/a.py", "VALUE=2\n")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with redirect_stdout(io.StringIO()):
            result = check.run_tests(self.root, ["tests/test_actual.py"])
        self.assertFalse(result["success"])


class ReceiptReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.engine = Watches(
            self.data / "workbench/watches.sqlite3", Store(self.data), lambda: 1000
        )
        self.task, self.plan = release_plan("pypi", "contract-demo", "1.2.3")
        self.body = {"info": {"name": "contract-demo", "version": "1.2.3"}, "urls": []}
        self.envelope = {
            "http_status": 200,
            "body": self.body,
            "url": "https://pypi.org/pypi/contract-demo/1.2.3/json",
            "headers": {},
            "headers_complete": True,
        }

    def submit(
        self,
        evidence=None,
        revision=1,
        task=None,
        plan=None,
        mode="live public pypi GET",
        watch=None,
    ):
        task, plan = task or self.task, plan or self.plan
        watch = watch or self.engine.create(task, plan)
        if evidence is None:
            with patch("ternela.connectors.read_json", return_value=deepcopy(self.envelope)):
                evidence, _ = collect(plan, task["context"], "after", task["provider"])
        receipt = self.engine.submit(watch["id"], str(revision), revision, evidence, mode=mode)[
            "receipt"
        ]
        return watch, receipt

    def case(self):
        return example(self.task, self.plan, self.submit()[1])

    def test_raw_renormalization_uses_no_network_and_detects_broken_adapter(self):
        case = self.case()
        with patch("ternela.connectors.read_json", side_effect=AssertionError("network")):
            self.assertTrue(replay(case)["passed"])

            class Broken(PyPIConnector):
                def normalize(self, body):
                    return dict(super().normalize(body), usable_files=1)

            old = CONNECTORS.get("pypi")
            broken = Broken(old.provider, old.host, old.operation, old.documentation)
            with patch("ternela.connectors.CONNECTORS", ConnectorRegistry((broken,))):
                result = replay(case)
        self.assertFalse(result["passed"])
        self.assertEqual(result["expected"]["status"], "pending")
        self.assertEqual(result["actual"]["status"], "complete")

    def test_request_and_destination_drift_cannot_hide_behind_same_outcome(self):
        case = self.case()
        for mutation in ("call", "url", "extra-page"):
            changed = deepcopy(case)
            if mutation == "call":
                changed["calls"]["release"][0]["bindings"]["path"]["package"] = "other"
            elif mutation == "url":
                changed["snapshots"]["release"][0]["url"] = "https://other.example/path"
            else:
                changed["snapshots"]["release"].append(deepcopy(changed["snapshots"]["release"][0]))
                changed["calls"]["release"].append(deepcopy(changed["calls"]["release"][0]))
            with self.subTest(mutation=mutation):
                self.assertFalse(replay(changed)["passed"])

    def test_harvest_cursor_deduplicates_times_and_does_not_modify_event_database(self):
        watch, _ = self.submit()
        self.submit(revision=2, watch=watch)
        before = (self.data / "workbench/watches.sqlite3").read_bytes()
        cases, report = harvest(self.data)
        self.assertEqual(report["added"], 1)
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(len(cases), 1)
        self.assertEqual((self.data / "workbench/watches.sqlite3").read_bytes(), before)
        packed = (self.data / "checks/receipts.json.gz").read_bytes()
        self.assertEqual(harvest(self.data)[1]["scanned"], 0)
        self.assertEqual((self.data / "checks/receipts.json.gz").read_bytes(), packed)

    def test_retention_bounds_preserve_provider_outcome_representation(self):
        original = self.case()
        cases = []
        for provider in ("a", "b"):
            for status in ("complete", "unknown"):
                for number in range(100):
                    case = deepcopy(original)
                    case["task"]["provider"] = provider
                    case["expected"]["status"] = status
                    case["id"] = f"{provider}-{status}-{number:04}"
                    cases.append(case)
        selected = retain(cases)
        self.assertLessEqual(len(selected), 24)
        self.assertEqual(
            {(c["task"]["provider"], c["expected"]["status"]) for c in selected},
            {("a", "complete"), ("a", "unknown"), ("b", "complete"), ("b", "unknown")},
        )
        self.assertEqual(retain(reversed(cases)), selected)

    def test_shape_distinguishes_zero_empty_boolean_and_large_exact_integer(self):
        self.assertEqual(
            len({canonical(shape(v)) for v in (0, 1, -1, 9007199254740993, False, "", [], [1])}), 8
        )
        case = self.case()
        case["snapshots"]["release"][0]["source_body"]["extra_integer"] = 9007199254740993
        self.assertEqual(
            json.loads(canonical(case))["snapshots"]["release"][0]["source_body"]["extra_integer"],
            9007199254740993,
        )
        self.assertTrue(replay(case)["passed"])

    def test_corrupt_corpus_and_oversized_blob_are_rejected(self):
        self.submit()
        harvest(self.data)
        path = self.data / "checks/receipts.json.gz"
        path.write_bytes(b"corrupt")
        with self.assertRaises(OSError):
            load_corpus(self.data)
        ref = self.engine.store.put({"value": "x" * 10000})
        with self.assertRaisesRegex(ValueError, "read limit"):
            self.engine.store.get(ref, max_bytes=100)

    def test_missing_raw_and_stale_receipts_are_explicit_skips(self):
        _, receipt = self.submit()
        del receipt["input"]["release"][0]["source_body"]
        with self.assertRaisesRegex(ValueError, "raw source"):
            example(self.task, self.plan, receipt)
        receipt["result"]["stale"] = True
        with self.assertRaisesRegex(ValueError, "freshness-only"):
            example(self.task, self.plan, receipt)

    def test_caller_guard_failure_and_ignored_input_are_replayable(self):
        template = next(t for t in catalog() if t["task"]["id"] == "n01")
        task, plan = template["task"], template["plan"]
        evidence = snapshots(task, plan, "quantity_string", "before")
        watch = self.engine.create(task, plan, "before")
        receipt = self.engine.submit(watch["id"], "guard", 1, evidence)["receipt"]
        self.assertTrue(receipt["result"]["preservation_failures"])
        self.assertTrue(replay(example(task, plan, receipt))["passed"])

    def test_missing_caller_snapshot_replays_implicit_unknown_response(self):
        watch = self.engine.create(self.task, self.plan)
        receipt = self.engine.submit(watch["id"], "missing", 1, {})["receipt"]
        self.assertTrue(replay(example(self.task, self.plan, receipt))["passed"])

    def test_failure_is_compared_before_harvest_can_evict_it(self):
        self.submit()
        harvest(self.data)
        original = (self.data / "checks/receipts.json.gz").read_bytes()
        failure = {"total": 1, "passed": 0, "failures": [{"id": "old", "issues": ["changed"]}]}
        with (
            patch("ternela.replay.compare", return_value=failure),
            patch("ternela.replay.harvest", side_effect=AssertionError("eviction attempted")),
            redirect_stdout(io.StringIO()),
        ):
            report = check.cycle(
                PROJECT, self.data, runner=lambda *args: dict(tests=1, files=1, success=True)
            )
        self.assertFalse(report["success"])
        self.assertEqual((self.data / "checks/receipts.json.gz").read_bytes(), original)

    def test_missing_blob_is_reported_without_becoming_a_fixture(self):
        _, receipt = self.submit()
        # Remove only this test's synthetic receipt blob.
        with self.engine.connect() as db:
            ref = json.loads(db.execute("SELECT receipt_ref FROM events").fetchone()[0])
        (self.data / "blobs" / ref["sha256"][:2] / (ref["sha256"] + ".gz")).unlink()
        cases, report = harvest(self.data)
        self.assertEqual(cases, [])
        self.assertTrue(report["skipped"])
        self.assertEqual(compare(cases)["total"], 0)

    def test_orphaned_event_advances_cursor_without_backlog_loop(self):
        with self.engine.connect() as db:
            db.execute("INSERT INTO events VALUES('missing-watch','event','fingerprint',1,1,'{}')")
        cases, report = harvest(self.data)
        self.assertEqual(cases, [])
        self.assertFalse(report["pending"])
        self.assertTrue(report["skipped"])

    def test_two_page_public_receipt_replays_through_existing_pagination(self):
        from ternela.providers.base import PublicConnector

        class Pages(PublicConnector):
            def contract(self):
                return {
                    "paths": {
                        "/objects": {
                            "get": {
                                "responses": {
                                    "200": {
                                        "content": {
                                            "application/json": {
                                                "schema": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {"id": {"type": "integer"}},
                                                        "required": ["id"],
                                                        "additionalProperties": False,
                                                    },
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

        connector = Pages(
            "pages",
            "pages.example",
            "GET /objects",
            "https://pages.example/docs",
            allow_query=True,
            allow_continuation=True,
        )
        task = dict(self.task, id="pages-test", provider="pages", context={})
        plan = deepcopy(self.plan)
        plan["task_id"] = task["id"]
        request = plan["requests"][0]
        request.update(operation="GET /objects", bindings_json="{}")
        request["pagination"].update(mode="link", items_pointer="", max_pages=3)
        plan["checks"] = [
            dict(
                plan["checks"][0],
                id="count",
                collection_pointer="",
                field_pointer="",
                selector_json="{}",
                select_mode="count",
                op="eq",
                value_json="2",
            )
        ]
        plan["requirements"] = [dict(plan["requirements"][0], check_ids=["count"])]
        first = {
            "http_status": 200,
            "body": [{"id": 1}],
            "headers": {"link": '<?page=2>; rel="next"'},
            "headers_complete": True,
            "url": "https://pages.example/objects",
        }
        second = dict(
            first, body=[{"id": 2}], headers={}, url="https://pages.example/objects?page=2"
        )
        with patch("ternela.connectors.CONNECTORS", ConnectorRegistry((connector,))):
            with patch("ternela.connectors.read_json", side_effect=[first, second]):
                captured, _ = collect(plan, {}, "after", "pages")
            watch = self.engine.create(task, plan)
            receipt = self.engine.submit(
                watch["id"], "pages", 1, captured, mode="live public pages GET"
            )["receipt"]
            self.assertEqual(receipt["result"]["status"], "complete")
            case = example(task, plan, receipt)
            with patch("ternela.connectors.read_json", side_effect=AssertionError("network")):
                self.assertTrue(replay(case)["passed"])
                case["snapshots"]["release"][1]["url"] = "https://pages.example/objects?page=3"
                self.assertFalse(replay(case)["passed"])

    def test_unchanged_receipts_reuse_verified_digest_and_tampering_invalidates_cache(self):
        self.submit()

        def runner(*args):
            return dict(tests=1, files=1, success=True)

        def no_repeated_work(cases):
            self.assertEqual(cases, [])
            return {"total": 0, "passed": 0, "failures": []}

        with redirect_stdout(io.StringIO()):
            self.assertTrue(check.cycle(PROJECT, self.data, runner=runner)["success"])
        with (
            patch(
                "ternela.replay.compare",
                side_effect=no_repeated_work,
            ),
            redirect_stdout(io.StringIO()),
        ):
            cached = check.cycle(PROJECT, self.data, incremental=True, runner=runner)
        self.assertTrue(cached["success"])
        self.assertEqual(cached["replay"]["cached"], 1)
        corpus = load_corpus(self.data)
        corpus["cases"][0]["expected"]["status"] = "complete"
        (self.data / "checks/receipts.json.gz").write_bytes(
            gzip.compress(canonical(corpus), mtime=0)
        )
        with redirect_stdout(io.StringIO()):
            changed = check.cycle(PROJECT, self.data, incremental=True, runner=runner)
        self.assertFalse(changed["success"])
        self.assertEqual(len(changed["replay"]["failures"]), 1)
