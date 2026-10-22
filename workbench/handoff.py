# SPDX-License-Identifier: Apache-2.0
"""Bounded UI adapter for the existing no-model, process/artifact handoff demo."""

import gzip
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from ternela.paths import DATA
from ternela.storage import PROJECT, write_json

LIMIT = 45


class HandoffDemo:
    def __init__(self, root=None):
        self.root = Path(root or DATA / "demos/ui").resolve()
        self.lock = threading.Lock()

    def current(self):
        latest = self.root / "latest.json"
        if not latest.exists():
            return {"status": "idle", "events": []}
        identity = json.loads(latest.read_text())["id"]
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise ValueError("Invalid saved demo identity")
        directory = self.root / identity
        record = json.loads((directory / "run.json").read_text())
        events = []
        timeline = directory / "demo/timeline.jsonl.gz"
        # A writer can be appending a gzip member; expose complete lines only.
        if timeline.exists():
            with gzip.open(timeline, "rt") as stream:
                try:
                    for line in stream:
                        events.append(json.loads(line))
                except (EOFError, gzip.BadGzipFile, ValueError):
                    pass
        result = dict(id=identity, status=record["status"], events=events)
        completed = directory / "demo/completed.json"
        if any(e["event"] == "done" for e in events) and completed.exists():
            ledger = json.loads(completed.read_text())
            result.update(status="completed", answer=ledger["answer"], watch_id=ledger["watch_id"])
            result["csv"] = (directory / "demo/sales.csv").read_text()
        elif record["status"] == "running" and time.time() - record["started_at"] > LIMIT + 5:
            result.update(status="failed", error="The demo was interrupted. Run it again to retry.")
        if result["status"] == "completed" and "answer" not in result:
            result.update(
                status="failed", error="The saved demo output is incomplete. Run it again to retry."
            )
        if record.get("error"):
            result["error"] = record["error"]
        return result

    def start(self):
        with self.lock:
            current = self.current()
            if current["status"] == "running":
                return current  # Double-clicks and other tabs join the same run.
            self.prune()
            identity = uuid.uuid4().hex
            directory = self.root / identity
            directory.mkdir(parents=True, mode=0o700)
            record = dict(id=identity, status="running", started_at=time.time())
            write_json(directory / "run.json", record)
            write_json(self.root / "latest.json", {"id": identity})
            thread = threading.Thread(target=self.work, args=(directory, record), daemon=True)
            try:
                thread.start()
            except RuntimeError:
                write_json(
                    directory / "run.json",
                    dict(
                        record,
                        status="failed",
                        error="Could not start the demo. Run it again to retry.",
                    ),
                )
                raise
            return self.current()

    def prune(self):
        previous = []
        for directory in self.root.glob("*"):
            if not directory.is_dir() or directory.is_symlink() or len(directory.name) != 32:
                continue
            marker = directory / "run.json"
            try:
                record = json.loads(marker.read_text())
                if record.get("id") == directory.name and record.get("status") in {
                    "completed",
                    "failed",
                }:
                    previous.append((record["started_at"], directory))
            except (OSError, ValueError, KeyError):
                continue
        for _, directory in sorted(previous, reverse=True)[5:]:
            shutil.rmtree(directory)

    def work(self, directory, record):
        log_path = directory / "runner.log"
        try:
            with log_path.open("wb") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "examples.agent_handoff",
                        "--dir",
                        str(directory / "demo"),
                        "--delay",
                        "5",
                        "--timeout",
                        "20",
                    ],
                    cwd=PROJECT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    code = process.wait(timeout=LIMIT)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    raise RuntimeError("The demo timed out. Run it again to retry.") from None
            if code or not (directory / "demo/completed.json").is_file():
                raise RuntimeError("The local export demo did not complete. Run it again to retry.")
            write_json(directory / "run.json", dict(record, status="completed"))
        except (OSError, RuntimeError) as exc:
            write_json(directory / "run.json", dict(record, status="failed", error=str(exc)))
        finally:
            if log_path.exists():
                (directory / "runner.log.gz").write_bytes(
                    gzip.compress(log_path.read_bytes(), mtime=0)
                )
                log_path.unlink()
