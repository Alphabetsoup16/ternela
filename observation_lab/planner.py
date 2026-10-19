# SPDX-License-Identifier: Apache-2.0
"""Run tools-disabled model inference on public task/catalog data only.

No scenario names, expected outputs, private evidence, or grader code are read
by this module. Auth is delegated to the CLI; no credential values are loaded.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .schema import BATCH, validate_shape

ROOT = Path(__file__).parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def generate(input_path, output_dir, strategy):
    cli = shutil.which("codex")
    if not cli:
        raise RuntimeError("Codex CLI is not installed; supply a generated plan JSON for offline grading instead")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "plans.json").exists():
        raise RuntimeError("Plans already exist. Use a new output directory to preserve the earlier run.")
    public_bytes = input_path.read_bytes()
    public = json.loads(public_bytes)
    if set(public) != {"tasks"}:
        raise ValueError("Planner input must contain only public tasks")
    prompt = (ROOT / "prompts" / f"{strategy}.txt").read_text() + "\n\nINPUT:\n" + json.dumps(public, indent=2)
    schema_bytes = (json.dumps(BATCH, indent=2) + "\n").encode()
    schema_path = output_dir / "schema.json"
    schema_path.write_bytes(schema_bytes)
    (output_dir / "public-input.json").write_bytes(public_bytes)
    (output_dir / "prompt.txt").write_text(prompt)
    metadata = {"started_at": datetime.now(timezone.utc).isoformat(), "strategy": strategy,
                "public_input_sha256": digest(public_bytes), "prompt_sha256": digest(prompt.encode()),
                "schema_sha256": digest(schema_bytes), "planner_module_sha256": digest(Path(__file__).read_bytes()),
                "backend": "Codex CLI default model, user config ignored; tools disabled",
                "cli_version": subprocess.check_output([cli, "--version"], text=True).strip(),
                "task_count": len(public["tasks"]), "private_scenarios_sent_to_model": False,
                "status": "running"}
    meta_path = output_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="observation-inference-") as inference_dir:
        args = [cli, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                "-C", inference_dir, "-s", "read-only", "-c", "features.shell_tool=false",
                "-c", "project_doc_max_bytes=0", "-c", 'web_search="disabled"',
                "-c", "apps._default.enabled=false", "--json", "--output-schema", str(schema_path),
                "-o", str(output_dir / "plans.json"), "-"]
        metadata["command"] = args
        try:
            with (output_dir / "events.jsonl").open("w") as events, (output_dir / "stderr.log").open("w") as err:
                completed = subprocess.run(args, input=prompt, text=True, stdout=events, stderr=err, timeout=900)
            metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
            event_rows = [json.loads(line) for line in (output_dir / "events.jsonl").read_text().splitlines() if line.strip()]
            item_types = sorted({row["item"]["type"] for row in event_rows if "item" in row})
            metadata["observed_item_types"] = item_types
            metadata["usage"] = [row["usage"] for row in event_rows if "usage" in row]
            # This inference backend must not call tools, including reading the private suite.
            if set(item_types) - {"agent_message", "reasoning"}:
                raise RuntimeError("Unexpected tool activity: this run cannot be treated as isolated inference")
            if completed.returncode:
                raise RuntimeError(f"Codex exited {completed.returncode}; see {output_dir / 'stderr.log'}")
            plans = json.loads((output_dir / "plans.json").read_text())
            validate_shape(plans)
            expected = {task["id"] for task in public["tasks"]}
            actual = [plan["task_id"] for plan in plans["plans"]]
            if set(actual) != expected or len(actual) != len(expected):
                raise ValueError("Missing, duplicate or unknown task in model output")
            metadata["plans_sha256"] = digest((output_dir / "plans.json").read_bytes())
            metadata["status"] = "completed"
        except Exception as exc:
            metadata["status"] = "failed"
            metadata["error"] = str(exc)
            raise
        finally:
            meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Generated {len(plans['plans'])} plans with {strategy}; {metadata['elapsed_seconds']:.1f}s; {output_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "fixtures/public.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strategy", choices=("direct", "coverage"), default="coverage")
    args = parser.parse_args()
    generate(args.input, args.output, args.strategy)


if __name__ == "__main__":
    main()
