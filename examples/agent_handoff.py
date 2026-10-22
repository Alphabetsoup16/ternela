# SPDX-License-Identifier: Apache-2.0
"""A real local export wakes an exited agent through Ternela's durable queue."""

import argparse
import csv
import fcntl
import gzip
import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from ternela.paths import DATA
from ternela.storage import PROJECT, write_json

from . import handoff_support as support


def say(root, event, text, **details):
    print(f"  {event.upper():<9} {text}", flush=True)
    record = dict(at=time.time(), event=event, text=text, **details)
    with gzip.open(root / "timeline.jsonl.gz", "ab") as stream:
        stream.write((json.dumps(record) + "\n").encode())


def agent(root, stage):
    config = support.configuration(root)
    if config["agent"] == "codex":
        from .handoff_codex import run

        return run(root, stage)
    command = "watch" if stage == "start" else "reference-finish"
    result = subprocess.run(
        [sys.executable, "-m", "examples.agent_handoff", command, "--dir", str(root)],
        cwd=PROJECT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    (root / f"reference-{stage}.log.gz").write_bytes(
        gzip.compress((result.stdout + result.stderr).encode(), mtime=0)
    )
    if result.returncode:
        raise RuntimeError("Reference agent failed: " + result.stderr[-1000:])
    return dict(completed=True)


def export(root, delay, fail=False):
    time.sleep(delay)
    if fail:
        support.publish(root, "failed", 1)
        raise RuntimeError("Export failed intentionally; no completed artifact")
    temporary = root / "sales.csv.tmp"
    with temporary.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("order_id", "net_cents", "currency"))
        writer.writerows(support.ROWS)
    temporary.replace(root / "sales.csv")
    support.publish(root, "completed", 0)


def consume(root, timeout):
    completed = root / "completed.json"
    if completed.exists():
        record = json.loads(completed.read_text())
        if json.loads((root / "answer.json").read_text()) != record["answer"]:
            raise ValueError("Completed demo answer changed; inspect it before retrying")
        if record["watch_id"] != support.watch_id(root):
            raise ValueError("Saved answer belongs to another watch")
        support.engine(root).acknowledge_delivery(
            record["watch_id"], support.CONSUMER, record["message_id"]
        )
        say(
            root,
            "done",
            "Already completed; acknowledgement recovered without another agent invocation.",
        )
        return record
    watches = support.engine(root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = watches.get(support.watch_id(root))["receipt"]
        observed = current["input"]["export"][0]["body"] if current else {}
        if observed.get("status") == "failed":
            raise RuntimeError("The export failed. Agent was not resumed; saved evidence retained.")
        message = support.next_delivery(root)
        if not message:
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            continue
        if message["receipt"]["result"]["status"] != "complete":
            watches.acknowledge_delivery(support.watch_id(root), support.CONSUMER, message["id"])
            say(root, "pending", "Condition not complete; agent stays stopped.")
            continue
        support.fresh_completion(root, message)
        say(
            root,
            "observed",
            "Export finished. CSV identity, digest and completion evidence verified.",
        )
        # Recovery after the local answer was committed but acknowledgement was interrupted.
        try:
            answer = support.verify_answer(root, message)
        except (ValueError, OSError):
            say(
                root,
                "resume",
                "Completion received. Launching the saved agent continuation.",
                message_id=message["id"],
            )
            agent(root, "finish")
            answer = support.verify_answer(root, message)
        else:
            say(root, "recover", "Valid saved answer found; no duplicate agent invocation.")
        record = dict(
            answer=answer,
            watch_id=support.watch_id(root),
            message_id=message["id"],
            agent=support.configuration(root)["agent"],
            session_id=support.configuration(root).get("session_id"),
            completed_at=time.time(),
        )
        write_json(completed, record)
        watches.acknowledge_delivery(support.watch_id(root), support.CONSUMER, message["id"])
        say(
            root,
            "done",
            f"{answer['orders']} orders · ${answer['total_cents'] / 100:.2f} USD. Answer verified; delivery acknowledged.",
        )
        return record
    raise TimeoutError("No completion before timeout. Saved watch and queue remain; use resume.")


def resume(root, timeout):
    support.configuration(root)
    with (root / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another runner owns this demo; no second agent was launched"
            ) from None
        return consume(root, timeout)


def start(root, mode, delay, timeout, fail, detach):
    root.mkdir(parents=True, exist_ok=False)
    root.chmod(0o700)
    write_json(root / "demo.json", dict(format=1, agent=mode, job_id=uuid.uuid4().hex))
    (root / "tools.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(PROJECT)!r})\n"
        "from examples.agent_handoff import main\n"
        "main([*sys.argv[1:], '--dir', str(Path(__file__).resolve().parent)])\n"
    )
    (root / "AGENTS.md").write_text(
        "This is an isolated Ternela export demo. Use tools.py watch to save the wait, "
        "then stop. When resumed, use tools.py inspect and read sales.csv to write answer.json. "
        "Do not modify tools.py, demo.json, watch.json, the database, source code or CSV. "
        "Do not poll, sleep, call external services, use other agents or acknowledge deliveries.\n"
    )
    label = (
        "Codex model; two turns using your existing CLI login"
        if mode == "codex"
        else "reference agent; no model call"
    )
    say(root, "agent", f"Summarize the export when ready ({label}).")
    agent(root, "start")
    watch = support.engine(root).get(support.watch_id(root))
    if watch["receipt"]["result"]["status"] != "pending" or (root / "answer.json").exists():
        raise RuntimeError("Initial agent did not stop at the pending watch")
    say(root, "watch", f"Saved {watch['id']} · pending. Initial agent process exited.")
    arguments = [
        "export",
        "--dir",
        str(root),
        "--delay",
        str(delay),
        *(["--fail-job"] if fail else []),
    ]
    with (root / "producer.log").open("wb") as log:
        if detach:
            # A bounded child can outlive this CLI without an unowned Popen handle.
            code = (
                f"import sys; sys.path.insert(0, {str(PROJECT)!r}); "
                "from examples.agent_handoff import main; main(sys.argv[1:])"
            )
            pid = os.posix_spawn(
                sys.executable,
                [sys.executable, "-c", code, *arguments],
                os.environ,
                file_actions=[
                    (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                    (os.POSIX_SPAWN_DUP2, log.fileno(), 1),
                    (os.POSIX_SPAWN_DUP2, log.fileno(), 2),
                ],
            )
            say(
                root,
                "job",
                "Bounded export process started; no agent is running.",
                producer_pid=pid,
            )
            say(
                root,
                "detach",
                f"Runner exits too. Resume later: python3 -m examples.agent_handoff resume --dir {root}",
            )
            return None
        producer = subprocess.Popen(
            [sys.executable, "-m", "examples.agent_handoff", *arguments],
            cwd=PROJECT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    say(
        root,
        "job",
        "Separate export process started. Agent is not running during this wait.",
        producer_pid=producer.pid,
    )
    try:
        return resume(root, timeout)
    finally:
        if producer.poll() is None:
            producer.terminate()
            try:
                producer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                producer.kill()
                producer.wait()
        if (root / "producer.log").exists():
            path = root / "producer.log"
            (root / "producer.log.gz").write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            path.unlink()


def seconds(value, minimum, maximum):
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise argparse.ArgumentTypeError(f"Use a duration from {minimum} to {maximum} seconds")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "resume", "watch", "inspect", "export", "reference-finish"],
    )
    parser.add_argument("--agent", choices=["reference", "codex"], default="reference")
    parser.add_argument(
        "--dir", type=Path, help="New isolated demo folder; use the same path to resume"
    )
    parser.add_argument("--delay", type=lambda v: seconds(v, 0, 30), default=3)
    parser.add_argument("--timeout", type=lambda v: seconds(v, 1, 600), default=120)
    parser.add_argument(
        "--fail-job",
        action="store_true",
        help="Show that a failed export does not resume the agent",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Exit after starting the bounded producer; resume later",
    )
    args = parser.parse_args(argv)
    if args.command != "run" and args.dir is None:
        parser.error("This command needs --dir pointing to an existing demo")
    root = (args.dir or DATA / "demos" / ("agent-" + uuid.uuid4().hex[:12])).absolute()
    if root.is_symlink():
        parser.error("Demo folder must not be a symbolic link")
    root = root.resolve()
    try:
        if args.command == "run":
            print("\nTernela · wait, leave, resume\n", flush=True)
            print(f"Demo folder: {root}\n", flush=True)
            start(root, args.agent, args.delay, args.timeout, args.fail_job, args.detach)
        elif args.command == "resume":
            resume(root, args.timeout)
        elif args.command == "watch":
            watch = support.create_watch(root)
            print(
                json.dumps(
                    {"watch_id": watch["id"], "decision": watch["receipt"]["result"]["status"]}
                )
            )
        elif args.command == "inspect":
            print(json.dumps(support.inspect(root)))
        elif args.command == "export":
            export(root, args.delay, args.fail_job)
        else:
            support.reference_finish(root)
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Demo stopped: {exc}\nSaved files: {root}\n")


if __name__ == "__main__":
    main()
