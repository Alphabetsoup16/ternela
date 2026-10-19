# SPDX-License-Identifier: Apache-2.0
"""Two automatic retrieval/inference rounds, with no private grading feedback."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from observation_lab.schema import validate_shape
from .schema import BATCH
from .source import Index, ROOT, fields, pointer


def sha(data):
    return hashlib.sha256(data).hexdigest()


def budget(excerpt, limit=14000):
    """Provider-independent excerpt cap; record that content was dropped."""
    excerpt = json.loads(json.dumps(excerpt))
    excerpt["description"] = excerpt.get("description", "")[:4000]
    dropped = 0
    while len(json.dumps(excerpt)) > limit:
        collections = [v for k, v in excerpt.items() if k in ("response_fields", "request_fields", "parameters", "fields") and isinstance(v, list) and len(v) > 1]
        if not collections:
            excerpt = {"operation": excerpt.get("operation"), "truncated": "Excerpt exceeded byte budget; query a more specific schema reference"}
            break
        max(collections, key=lambda v: len(json.dumps(v))).pop()
        dropped += 1
    if dropped:
        excerpt["budget_truncation"] = {"dropped_tail_rows": dropped, "limit_characters": limit}
    return excerpt


def infer(payload, directory, schema=BATCH, prompt_path=None):
    directory.mkdir(parents=True, exist_ok=True)
    cli = shutil.which("codex")
    if not cli:
        raise RuntimeError("Codex CLI missing")
    prompt = (prompt_path or ROOT / "prompt.txt").read_text() + "\n\nINPUT:\n" + json.dumps(payload, separators=(",", ":"))
    (directory / "input.json").write_text(json.dumps(payload, indent=2) + "\n")
    (directory / "prompt.txt").write_text(prompt)
    schema_path = directory / "schema.json"
    schema_path.write_text(json.dumps(schema, indent=2) + "\n")
    metadata = {"started_at": datetime.now(timezone.utc).isoformat(), "prompt_sha256": sha(prompt.encode()),
                "private_audit_sent": False, "prompt_characters": len(prompt), "round": payload["round"],
                "cli_version": subprocess.check_output([cli, "--version"], text=True).strip(),
                "backend": "Codex CLI default model; user config ignored; no shell/web/apps",
                "status": "running"}
    meta_path = directory / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="api-doc-inference-") as work:
            command = [cli, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                       "-C", work, "-s", "read-only", "-c", "features.shell_tool=false", "-c", "project_doc_max_bytes=0",
                       "-c", 'web_search="disabled"', "-c", "apps._default.enabled=false", "--json",
                       "--output-schema", str(schema_path.resolve()), "-o", str((directory / "plans.json").resolve()), "-"]
            with (directory / "events.jsonl").open("w") as out, (directory / "stderr.log").open("w") as err:
                completed = subprocess.run(command, input=prompt, text=True, stdout=out, stderr=err, timeout=900)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines() if line.strip()]
        types = sorted({row["item"]["type"] for row in events if "item" in row})
        metadata["observed_item_types"] = types
        metadata["usage"] = [r["usage"] for r in events if "usage" in r]
        if completed.returncode or set(types) - {"agent_message", "reasoning"}:
            raise RuntimeError("Inference failed or used unexpected tools; inspect event log")
        result = json.loads((directory / "plans.json").read_text())
        validate_shape(result, schema)
        expected_ids = {t["id"] for t in payload["tasks"]}
        ids = [p["task_id"] for p in result["plans"]]
        if set(ids) != expected_ids or len(ids) != len(expected_ids):
            raise ValueError("Missing, duplicate or unknown task identity")
        metadata["plans_sha256"] = sha((directory / "plans.json").read_bytes())
        metadata["status"] = "completed"
        return result
    except Exception as exc:
        metadata["status"] = "failed"
        metadata["error"] = str(exc)
        raise
    finally:
        metadata["elapsed_seconds"] = round(time.monotonic() - start, 3)
        meta_path.write_text(json.dumps(metadata, indent=2) + "\n")
        print(f"Round {payload['round']}: {metadata['status']}; {metadata['elapsed_seconds']}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("results/docs/v1"))
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new output directory to preserve the existing run")
    args.output.mkdir(parents=True)
    public_bytes = (ROOT / "fixtures/public.json").read_bytes()
    public = json.loads(public_bytes)
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text())
    # Only hashes, not private requirements, enter run metadata.
    if sha(public_bytes) != manifest["public.json"]:
        raise ValueError("Frozen public task hash mismatch")
    source_manifest = json.loads((ROOT / "sources/manifest.json").read_text())
    for source in source_manifest:
        if sha((ROOT / "sources" / f"{source['provider']}.json").read_bytes()) != source["sha256"]:
            raise ValueError("Source snapshot changed")
    indexes = {p: Index(p) for p in {task["provider"] for task in public["tasks"]}}
    documents, retrievals = {}, {}
    for task in public["tasks"]:
        index = indexes[task["provider"]]
        hits = index.search(task["task"], top_k=12)
        chosen = [h["operation"] for h in hits[:4]]
        retrievals[task["id"]] = {"initial_query": task["task"], "candidate_hits": hits,
                                   "operations": chosen, "schema_refs": []}
        for key in chosen:
            documents[task["provider"] + "::" + key] = budget(index.excerpt(key))
    metadata = {"fixture_manifest": manifest, "sources": source_manifest,
                "code_sha256": {p.name: sha(p.read_bytes()) for p in sorted(ROOT.glob("*.py"))},
                "retrieval": "BM25 over every official operation's path, summary, description, tags and parameters; no task-specific endpoint mappings",
                "limits": {"initial_excerpt_operations_per_task": 4, "initial_candidate_names": 12,
                           "followup_queries_per_task": 3, "results_per_followup": 2,
                           "max_operations_per_task": 10, "max_schema_refs_per_task": 3,
                           "excerpt_character_budget": 14000, "inference_rounds": 2},
                "hand_authored_catalogs": False, "private_feedback_to_model": False}
    (args.output / "experiment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    payload = {"round": 1, "tasks": public["tasks"], "source_info": source_manifest,
               "retrieval": retrievals, "documents": documents}
    print(f"Round 1: {len(documents)} automatic operation excerpts; {len(json.dumps(payload))} input characters", flush=True)
    first = infer(payload, args.output / "round-1")
    tasks = {t["id"]: t for t in public["tasks"]}
    for plan in first["plans"]:
        task = tasks[plan["task_id"]]
        index = indexes[task["provider"]]
        record = retrievals[task["id"]]
        record["followup_searches"] = []
        for query in plan["followup_queries"][:3]:
            hits = index.search(query, 2)
            record["followup_searches"].append({"query": query, "hits": hits})
            for hit in hits:
                key = hit["operation"]
                if key not in record["operations"] and len(record["operations"]) < 10:
                    record["operations"].append(key)
                    documents[task["provider"] + "::" + key] = budget(index.excerpt(key))
        for ref in plan["schema_refs"][:3]:
            try:
                if not ref.startswith(("#/components/schemas/", "#/definitions/")):
                    raise KeyError(ref)
                rows = fields(index.spec, pointer(index.spec, ref), max_depth=5)
                content = budget({"operation": "SCHEMA " + ref, "fields": rows})
            except (KeyError, ValueError, TypeError):
                content = {"operation": "SCHEMA " + ref, "error": "Reference not found in the official source"}
            record["schema_refs"].append(ref)
            documents[task["provider"] + "::SCHEMA " + ref] = content
    payload = {"round": 2, "tasks": public["tasks"], "source_info": source_manifest,
               "retrieval": retrievals, "documents": documents, "previous_proposals": first}
    print(f"Round 2: {len(documents)} automatic excerpts; {len(json.dumps(payload))} input characters", flush=True)
    second = infer(payload, args.output / "round-2")
    (args.output / "plans.json").write_text(json.dumps(second, indent=2) + "\n")
    print(f"Saved final plans: {args.output / 'plans.json'}", flush=True)


if __name__ == "__main__":
    main()
