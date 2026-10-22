# SPDX-License-Identifier: Apache-2.0
"""Tool-disabled CLI inference with compressed provenance and bounded retries."""

import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ternela.schema import validate_shape
from ternela.storage import sha, write_json


def infer(payload, stage, schema, instructions, manifest_path, manifest, store):
    prompt = instructions + "\n\nINPUT:\n" + json.dumps(payload, separators=(",", ":"))
    if len(prompt) > 950000:
        raise ValueError("Evidence exceeds the recorded CLI context budget")
    record = {
        "input": store.put(payload),
        "prompt": store.put(prompt, "text/plain"),
        "schema": store.put(schema),
        "attempts": [],
        "status": "running",
    }
    if manifest.get("reuse_from"):
        previous_path = store.root / "runs" / manifest["reuse_from"] / "manifest.json"
        previous = json.loads(previous_path.read_text()).get("stages", {}).get(stage)
        if (
            previous
            and previous["status"] == "completed"
            and all(previous[k]["sha256"] == record[k]["sha256"] for k in ["input", "schema"])
        ):
            original_prompt = store.get(previous["prompt"])
            prefix = instructions + "\n\nINPUT:\n"
            if (
                original_prompt.startswith(prefix)
                and json.loads(original_prompt[len(prefix) :]) == payload
            ):
                # JSON key order can change when a canonical blob is loaded. Reuse
                # the original exact prompt serialization after validating its data.
                record = dict(
                    previous,
                    reused_from=manifest["reuse_from"],
                    reuse_validation="Same instructions, canonical payload and schema; original exact prompt retained",
                )
                manifest["stages"][stage] = record
                write_json(manifest_path, manifest)
                print(
                    stage, "reused exact completed prompt from", manifest["reuse_from"], flush=True
                )
                return store.get(record["output"])
    manifest["stages"][stage] = record
    write_json(manifest_path, manifest)
    cli = shutil.which("codex")
    if not cli:
        raise RuntimeError("The signed-in Codex CLI is required")
    for number in [1, 2]:
        attempt = {
            "number": number,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
            "prompt_sha256": sha(prompt.encode()),
            "timeout_seconds": 240,
        }
        record["attempts"].append(attempt)
        write_json(manifest_path, manifest)
        start = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="observation-inference-") as temporary:
                work = Path(temporary)
                schema_path = work / "schema.json"
                schema_path.write_text(json.dumps(schema))
                output = work / "plans.json"
                command = [
                    cli,
                    "exec",
                    "--ignore-user-config",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "-C",
                    temporary,
                    "-s",
                    "read-only",
                    "-c",
                    "features.shell_tool=false",
                    "-c",
                    "project_doc_max_bytes=0",
                    "-c",
                    'web_search="disabled"',
                    "-c",
                    "apps._default.enabled=false",
                    "--json",
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output),
                    "-",
                ]
                with (work / "events").open("w") as stdout, (work / "stderr").open("w") as stderr:
                    try:
                        completed = subprocess.run(
                            command,
                            input=prompt,
                            text=True,
                            stdout=stdout,
                            stderr=stderr,
                            timeout=240,
                        )
                    finally:
                        record_events = (work / "events").read_text()
                        attempt["events"] = store.put(record_events, "application/x-ndjson")
                        attempt["stderr"] = store.put((work / "stderr").read_text(), "text/plain")
                events = [json.loads(line) for line in record_events.splitlines() if line.strip()]
                kinds = sorted({row["item"]["type"] for row in events if "item" in row})
                attempt.update(
                    usage=[row["usage"] for row in events if "usage" in row], item_types=kinds
                )
                if completed.returncode or set(kinds) - {"agent_message", "reasoning"}:
                    raise RuntimeError("CLI inference failed or attempted tools")
                result = json.loads(output.read_text())
                # A completed but malformed answer is retained and is not retried.
                record["output"] = store.put(result)
                attempt["status"] = "completed"
                validate_shape(result, schema)
                if sorted(p["task_id"] for p in result["plans"]) != sorted(
                    t["id"] for t in payload["tasks"]
                ):
                    raise ValueError("Missing, duplicate or unknown task identity")
                record["status"] = "completed"
                return result
        except Exception as exc:
            attempt["error"] = str(exc)
            if attempt["status"] == "completed":
                record["status"] = "invalid_completed_output"
                raise
            attempt["status"] = "failed"
            if number == 2:
                record["status"] = "failed"
                raise
        finally:
            attempt["elapsed_seconds"] = round(time.monotonic() - start, 3)
            write_json(manifest_path, manifest)
            print(
                stage,
                "attempt",
                number,
                attempt["status"],
                attempt["elapsed_seconds"],
                "seconds",
                flush=True,
            )
