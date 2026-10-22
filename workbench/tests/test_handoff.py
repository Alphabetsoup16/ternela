# SPDX-License-Identifier: Apache-2.0
"""The UI must launch the real handoff, preserve progress, and enforce local auth."""

import gzip
import json
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from examples import handoff_support as support
from ternela.storage import write_json
from workbench.handoff import HandoffDemo
from workbench.server import Handler


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.demo = HandoffDemo(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def saved(self, status="running", started=None):
        identity = "a" * 32
        directory = self.root / identity
        write_json(self.root / "latest.json", {"id": identity})
        write_json(
            directory / "run.json",
            dict(id=identity, status=status, started_at=started or time.time()),
        )
        return directory

    def test_real_processes_complete_and_reload_recovers_the_same_result(self):
        first = self.demo.start()
        self.assertEqual(first["status"], "running")
        self.assertEqual(self.demo.start()["id"], first["id"])
        seen_stopped = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self.demo.current()
            seen_stopped |= state["status"] == "running" and any(
                e["event"] == "watch" for e in state["events"]
            )
            if state["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(state["status"], "completed", state)
        self.assertTrue(seen_stopped)
        self.assertEqual(state["answer"]["total_cents"], 6800)
        self.assertEqual(state["answer"]["orders"], 3)
        self.assertEqual(state, HandoffDemo(self.root).current())
        events = [e["event"] for e in state["events"]]
        self.assertLess(events.index("watch"), events.index("observed"))
        self.assertLess(events.index("observed"), events.index("resume"))
        self.assertLess(events.index("resume"), events.index("done"))
        folder = self.root / state["id"] / "demo"
        self.assertEqual(support.engine(folder).deliveries(state["watch_id"], support.CONSUMER), [])
        # The worker may be finishing log compression after publishing completion.
        while (
            not (folder.parent / "runner.log.gz").exists()
            or (folder.parent / "runner.log").exists()
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue((folder.parent / "runner.log.gz").is_file())

    def test_process_failure_is_saved_without_a_success_or_answer(self):
        directory = self.saved()
        record = json.loads((directory / "run.json").read_text())
        with patch(
            "workbench.handoff.subprocess.Popen", side_effect=OSError("process unavailable")
        ):
            self.demo.work(directory, record)
        state = self.demo.current()
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("answer", state)
        self.assertIn("process unavailable", state["error"])

    def test_timeout_terminates_only_the_owned_process_group(self):
        directory = self.saved()
        record = json.loads((directory / "run.json").read_text())
        process = Mock(pid=90210)
        process.wait.side_effect = [subprocess.TimeoutExpired("demo", 45), 0]
        with (
            patch("workbench.handoff.subprocess.Popen", return_value=process),
            patch("workbench.handoff.os.killpg") as kill,
        ):
            self.demo.work(directory, record)
        kill.assert_called_once_with(90210, signal.SIGTERM)
        self.assertEqual(self.demo.current()["status"], "failed")
        self.assertIn("timed out", self.demo.current()["error"])

    def test_partial_compressed_event_does_not_invent_progress(self):
        directory = self.saved()
        p = directory / "demo/timeline.jsonl.gz"
        p.parent.mkdir()
        raw = gzip.compress(b'{"event":"watch","at":1}\n')
        for data in (raw[:-8], raw[:1], raw[:10]):
            with self.subTest(data=data):
                p.write_bytes(data)
                state = self.demo.current()
                self.assertEqual(state["status"], "running")
                self.assertFalse(any(e["event"] == "done" for e in state["events"]))

    def test_interrupted_and_incomplete_runs_fail_visibly(self):
        self.saved(started=time.time() - 100)
        self.assertEqual(self.demo.current()["status"], "failed")
        self.saved(status="completed")
        self.assertEqual(self.demo.current()["status"], "failed")
        self.assertNotIn("answer", self.demo.current())

    def test_saved_identity_cannot_escape_demo_directory(self):
        write_json(self.root / "latest.json", {"id": "../elsewhere"})
        with self.assertRaises(ValueError):
            self.demo.current()

    def test_retention_removes_only_older_owned_finished_runs(self):
        for number in range(7):
            identity = f"{number:032x}"
            write_json(
                self.root / identity / "run.json",
                dict(id=identity, status="completed", started_at=number),
            )
        unrelated = self.root / "my-artifact"
        unrelated.mkdir()
        self.demo.prune()
        self.assertTrue(unrelated.exists())
        self.assertEqual(len(list(self.root.glob("*/run.json"))), 5)


class HandoffHTTPTests(unittest.TestCase):
    def test_local_token_origin_and_fixed_reference_mode(self):
        with tempfile.TemporaryDirectory() as root:
            manager = HandoffDemo(root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.service = SimpleNamespace(token="local-only", handoff=manager)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/api/agent-demo", timeout=5) as response:
                    self.assertEqual(json.load(response)["status"], "idle")
                for body, token, origin, expected in (
                    ({}, "wrong", base, 403),
                    ({}, "local-only", "https://example.com", 403),
                    ({"agent": "codex"}, "local-only", base, 400),
                    ({"dir": "/tmp/other"}, "local-only", base, 400),
                ):
                    request = Request(
                        base + "/api/agent-demo",
                        data=json.dumps(body).encode(),
                        headers={
                            "Content-Type": "application/json",
                            "X-Observation-Token": token,
                            "Origin": origin,
                        },
                    )
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request, timeout=5)
                    self.assertEqual(error.exception.code, expected)
                    error.exception.close()
                with patch.object(
                    manager,
                    "start",
                    return_value={"id": "fixed", "status": "running", "events": []},
                ) as start:
                    request = Request(
                        base + "/api/agent-demo",
                        data=b"{}",
                        headers={
                            "Content-Type": "application/json",
                            "X-Observation-Token": "local-only",
                            "Origin": base,
                        },
                    )
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 202)
                    start.assert_called_once_with()
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
