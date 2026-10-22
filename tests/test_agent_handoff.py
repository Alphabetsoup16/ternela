# SPDX-License-Identifier: Apache-2.0
"""A real producer, stopped agent and recoverable consumer must obey evidence order."""

import fcntl
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from examples import agent_handoff as demo
from examples import handoff_codex as codex
from examples import handoff_support as support
from ternela.storage import PROJECT, write_json
from ternela.watches import Watches


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        write_json(
            self.root / "demo.json", dict(format=1, agent="reference", job_id="exact-export")
        )
        support.create_watch(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def complete(self):
        demo.export(self.root, 0)
        watches = support.engine(self.root)
        pending = support.next_delivery(self.root)
        watches.acknowledge_delivery(support.watch_id(self.root), support.CONSUMER, pending["id"])
        return support.next_delivery(self.root)

    def test_reference_runs_real_processes_and_writes_verified_answer(self):
        path = self.root / "real-run"
        result = subprocess.run(
            [sys.executable, "-m", "examples.agent_handoff", "--dir", str(path), "--delay", "0.2"],
            cwd=PROJECT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads((path / "completed.json").read_text())
        self.assertEqual(record["agent"], "reference")
        self.assertIsNone(record["session_id"])
        self.assertEqual(record["answer"]["total_cents"], 6800)
        self.assertEqual(record["answer"]["orders"], 3)
        self.assertIn("no model call", result.stdout)
        self.assertIn("Initial agent process exited", result.stdout)
        self.assertEqual(
            support.engine(path).deliveries(support.watch_id(path), support.CONSUMER), []
        )

    def test_event_survives_runner_exit_before_continuation(self):
        path = self.root / "detached"
        args = [sys.executable, "-m", "examples.agent_handoff"]
        initial = subprocess.run(
            args + ["--dir", str(path), "--detach", "--delay", "0.1"],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(initial.returncode, 0, initial.stderr)
        self.assertFalse((path / "answer.json").exists())
        resumed = subprocess.run(
            args + ["resume", "--dir", str(path)],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads((path / "answer.json").read_text())["total_cents"], 6800)
        self.assertIsNone(support.next_delivery(path))

    def test_failed_real_export_exits_without_answer(self):
        path = self.root / "failed"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.agent_handoff",
                "--dir",
                str(path),
                "--fail-job",
                "--delay",
                "0",
            ],
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("export failed", result.stderr)
        self.assertFalse((path / "answer.json").exists())

    def test_unbounded_wait_options_are_rejected_before_creating_a_demo(self):
        for option, value in (
            ("--delay", "nan"),
            ("--delay", "-1"),
            ("--timeout", "inf"),
            ("--timeout", "0"),
        ):
            with self.subTest(option=option, value=value):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "examples.agent_handoff",
                        "--dir",
                        str(self.root / "unused"),
                        option,
                        value,
                    ],
                    cwd=PROJECT,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.root / "unused").exists())

    def test_pending_decision_never_invokes_continuation(self):
        with patch.object(demo, "agent") as agent:
            with self.assertRaises(TimeoutError):
                demo.consume(self.root, 0.01)
        agent.assert_not_called()
        self.assertFalse((self.root / "answer.json").exists())

    def test_wrong_export_identity_cannot_complete(self):
        self.complete()
        watches = support.engine(self.root)
        body = dict(
            watches.get(support.watch_id(self.root))["receipt"]["input"]["export"][0]["body"],
            job_id="other-export",
        )
        watches.submit(
            support.watch_id(self.root),
            "wrong-export",
            3,
            {"export": [{"http_status": 200, "headers": {}, "body": body}]},
        )
        self.assertEqual(
            watches.get(support.watch_id(self.root))["receipt"]["result"]["status"], "pending"
        )
        with patch.object(demo, "agent") as agent, self.assertRaises(ValueError):
            demo.consume(self.root, 1)
        agent.assert_not_called()

    def test_failure_event_does_not_resume_or_consume_failure(self):
        support.publish(self.root, "failed", 1)
        with (
            patch.object(demo, "agent") as agent,
            self.assertRaisesRegex(RuntimeError, "export failed"),
        ):
            demo.consume(self.root, 1)
        agent.assert_not_called()
        self.assertIsNotNone(support.next_delivery(self.root))

    def test_expired_success_is_not_authority_to_resume(self):
        self.complete()
        from ternela.storage import Store

        expired = Watches(
            self.root / "data/watches.sqlite3",
            Store(self.root / "data"),
            clock=lambda: time.time() + 301,
        )
        with patch.object(support, "engine", return_value=expired):
            with patch.object(demo, "agent") as agent, self.assertRaises(ValueError):
                demo.consume(self.root, 1)
        agent.assert_not_called()
        self.assertIsNotNone(support.next_delivery(self.root))

    def test_changed_artifact_blocks_continuation(self):
        self.complete()
        path = self.root / "sales.csv"
        path.write_text(path.read_text().replace("4200", "4201"))
        with (
            patch.object(demo, "agent") as agent,
            self.assertRaisesRegex(ValueError, "no longer matches"),
        ):
            demo.consume(self.root, 1)
        agent.assert_not_called()

    def test_missing_or_linked_artifact_is_rejected(self):
        self.complete()
        path = self.root / "sales.csv"
        path.rename(self.root / "elsewhere.csv")
        with self.assertRaises(ValueError):
            support.sales(self.root)
        path.symlink_to(self.root / "elsewhere.csv")
        with self.assertRaises(ValueError):
            support.sales(self.root)

    def test_malformed_csv_values_are_rejected(self):
        header = "order_id,net_cents,currency\n"
        for rows in (
            "a,1,USD,extra\nb,2,USD\nc,3,USD\n",
            "a,1,USD\nb,2,USD\nc,3\n",
            "a,1,USD\nb,2,USD\nc\n",
            ",1,USD\nb,2,USD\nc,3,USD\n",
        ):
            with self.subTest(rows=rows):
                (self.root / "sales.csv").write_text(header + rows)
                with self.assertRaises(ValueError):
                    support.sales(self.root)

    def test_bad_agent_answer_stays_unacknowledged(self):
        message = self.complete()

        def bad_answer(root, stage):
            support.reference_finish(root)
            p = root / "answer.json"
            record = json.loads(p.read_text())
            record["total_cents"] = 1
            write_json(p, record)

        with patch.object(demo, "agent", side_effect=bad_answer), self.assertRaises(ValueError):
            demo.consume(self.root, 1)
        self.assertEqual(support.next_delivery(self.root)["id"], message["id"])
        self.assertFalse((self.root / "completed.json").exists())

    def test_agent_error_retains_delivery(self):
        message = self.complete()
        with patch.object(demo, "agent", side_effect=RuntimeError("model unavailable")):
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                demo.consume(self.root, 1)
        self.assertEqual(support.next_delivery(self.root)["id"], message["id"])

    def test_saved_answer_recovers_without_another_agent_call(self):
        self.complete()
        support.reference_finish(self.root)
        with patch.object(demo, "agent", side_effect=AssertionError("must not run")):
            result = demo.resume(self.root, 1)
            self.assertEqual(result, demo.resume(self.root, 1))
        self.assertIsNone(support.next_delivery(self.root))

    def test_crash_after_answer_ledger_before_ack_recovers(self):
        message = self.complete()
        with patch.object(
            demo, "agent", side_effect=lambda root, stage: support.reference_finish(root)
        ):
            with patch.object(
                Watches, "acknowledge_delivery", side_effect=OSError("ack unavailable")
            ):
                with self.assertRaises(OSError):
                    demo.consume(self.root, 1)
        self.assertTrue((self.root / "completed.json").is_file())
        self.assertEqual(support.next_delivery(self.root)["id"], message["id"])
        with patch.object(demo, "agent", side_effect=AssertionError("must not repeat action")):
            demo.resume(self.root, 1)
        self.assertIsNone(support.next_delivery(self.root))

    def test_two_controllers_cannot_resume_the_same_agent(self):
        with (self.root / "controller.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                patch.object(demo, "agent") as agent,
                self.assertRaisesRegex(RuntimeError, "Another runner"),
            ):
                demo.resume(self.root, 1)
        agent.assert_not_called()

    def test_duplicate_producer_event_creates_no_extra_completion(self):
        self.complete()
        before = support.engine(self.root).outbox(support.watch_id(self.root))
        support.publish(self.root, "completed", 0)
        after = support.engine(self.root).outbox(support.watch_id(self.root))
        self.assertEqual(before, after)

    def test_local_demo_does_not_claim_a_public_connector(self):
        watch = support.engine(self.root).get(support.watch_id(self.root))
        self.assertFalse(watch["live_available"])
        self.assertEqual(
            watch["task"]["_admission"]["pinned_source"]["kind"], "demo_local_observation_contract"
        )


class CodexHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.session = "8f60c261-9dcc-4057-ae8a-11b4cd5b5dd1"
        write_json(self.root / "demo.json", dict(format=1, agent="codex", job_id="export"))

    def tearDown(self):
        self.temporary.cleanup()

    def response(self, session=None, completed=True, code=0):
        events = [{"type": "thread.started", "thread_id": session or self.session}]
        events.append({"type": "turn.completed" if completed else "turn.failed"})
        return Mock(returncode=code, stdout="\n".join(json.dumps(e) for e in events), stderr="")

    def test_actual_session_identifier_and_workspace_permissions_are_preserved(self):
        with patch.object(codex.shutil, "which", return_value="/codex"):
            with patch.object(codex.subprocess, "run", return_value=self.response()) as run:
                codex.run(self.root, "start")
                codex.run(self.root, "finish")
        self.assertEqual(support.configuration(self.root)["session_id"], self.session)
        args = run.call_args.args[0]
        self.assertIn("resume", args)
        self.assertIn(self.session, args)
        self.assertIn("workspace-write", args)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn("--last", args)
        self.assertNotIn("--model", args)

    def test_missing_cli_fails_clearly_without_reference_fallback(self):
        with patch.object(codex.shutil, "which", return_value=None), self.assertRaises(ValueError):
            codex.run(self.root, "start")
        self.assertNotIn("session_id", support.configuration(self.root))

    def test_failed_turn_is_not_success_even_with_exit_zero(self):
        with patch.object(codex.shutil, "which", return_value="/codex"):
            with patch.object(codex.subprocess, "run", return_value=self.response(completed=False)):
                with self.assertRaises(RuntimeError):
                    codex.run(self.root, "start")
        self.assertTrue((self.root / "codex-start.jsonl.gz").is_file())
        self.assertNotIn("session_id", support.configuration(self.root))

    def test_wrong_resumed_thread_is_rejected(self):
        config = support.configuration(self.root)
        write_json(self.root / "demo.json", dict(config, session_id=self.session))
        other = "1a9503b2-e80b-407f-9268-b8cec71992ba"
        with patch.object(codex.shutil, "which", return_value="/codex"):
            with patch.object(codex.subprocess, "run", return_value=self.response(session=other)):
                with self.assertRaisesRegex(RuntimeError, "different session"):
                    codex.run(self.root, "finish")


if __name__ == "__main__":
    unittest.main()
