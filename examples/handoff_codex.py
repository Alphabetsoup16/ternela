# SPDX-License-Identifier: Apache-2.0
"""Two bounded Codex CLI turns; resume the exact recorded session after delivery."""

import gzip
import json
import shutil
import subprocess
import uuid

from ternela.storage import write_json

from .handoff_support import configuration


def run(root, stage):
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is required; or use --agent reference for the no-model demo")
    config = configuration(root)
    args = [executable, "exec", "--sandbox", "workspace-write", "--cd", str(root)]
    if stage == "start":
        prompt = (
            "Your task is to summarize sales.csv after its export completes. It is not ready yet. "
            "Run `python3 tools.py watch` once to create a Ternela watch, then END THIS TURN. "
            "Do not poll, sleep, create sales.csv or write answer.json. Ternela will resume this "
            "same session when the actual local export completes. Use only this demo workspace."
        )
        args += ["--json", "--skip-git-repo-check", prompt]
    else:
        session = config.get("session_id", "")
        uuid.UUID(session)
        prompt = (
            "Ternela delivered a completion for your saved export watch. Continue your original "
            "task now. Run `python3 tools.py inspect` to check current evidence and obtain the "
            "delivery_key, job_id, artifact_sha256 and required answer fields. Read sales.csv, "
            "count its orders and sum net_cents. Write answer.json with EXACTLY these fields: "
            "job_id, orders (integer), total_cents (integer), currency, artifact_sha256, "
            "delivery_key. Compute from the actual CSV; do not invent data. Do not acknowledge "
            "the queue yourself or change demo/control files. The runner validates the answer "
            "before acknowledging. End this turn after writing the answer."
        )
        args += ["resume", "--json", "--skip-git-repo-check", session, prompt]
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        if isinstance(raw, str):
            raw = raw.encode()
        (root / f"codex-{stage}.jsonl.gz").write_bytes(gzip.compress(raw, mtime=0))
        raise RuntimeError("Codex turn timed out; delivery remains unacknowledged") from None
    for name, raw in (("jsonl", result.stdout), ("stderr", result.stderr)):
        (root / f"codex-{stage}.{name}.gz").write_bytes(gzip.compress(raw.encode(), mtime=0))
    events = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    sessions = [e["thread_id"] for e in events if e.get("type") == "thread.started"]
    completed = any(e.get("type") == "turn.completed" for e in events)
    if result.returncode or not completed:
        raise RuntimeError(f"Codex {stage} failed; inspect compressed logs in {root}")
    if stage == "start":
        if len(sessions) != 1:
            raise RuntimeError("Codex did not return one resumable session ID")
        uuid.UUID(sessions[0])
        config["session_id"] = sessions[0]
        write_json(root / "demo.json", config)
    elif sessions and any(session != config["session_id"] for session in sessions):
        raise RuntimeError("Codex resumed a different session")
    return dict(session_id=configuration(root)["session_id"], completed=True)
