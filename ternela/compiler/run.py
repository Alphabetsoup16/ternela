# SPDX-License-Identifier: Apache-2.0
"""Run fixed/full/adaptive evidence compilation with compressed experiment records."""

import argparse
import json
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from ternela.paths import DATA, source
from ternela.pinned_sources import SourceIndex
from ternela.storage import PROJECT, Store, sha, write_json
from ternela.validation import admit

from .evidence import Evidence
from .guides import load as load_guides
from .inference import infer
from .schema import BATCH, DISCOVERY

ROOT = Path(__file__).parent / "prompts"
FIXTURES = {"regression": "expansion_lab/fresh"}


def code_manifest():
    files = []
    for path in (PROJECT / "ternela").rglob("*"):
        if path.suffix in {".py", ".txt"} and not any(
            part in {"tests", "__pycache__", "_resources"}
            for part in path.relative_to(PROJECT).parts
        ):
            files.append(path)
    files += [ROOT / "protocol.json", PROJECT / "ternela/brand.json"]
    return {str(p.relative_to(PROJECT)): sha(p.read_bytes()) for p in sorted(files)}


def main(
    argv=None,
    *,
    fixtures=None,
    index_class=SourceIndex,
    evidence_class=Evidence,
    extra_instructions="",
):
    fixtures = fixtures or FIXTURES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["fixed", "adaptive", "full"], required=True)
    parser.add_argument("--suite", choices=list(fixtures), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument(
        "--reuse-from",
        help="Reuse only completed stages whose input, prompt and schema hashes match exactly",
    )
    args = parser.parse_args(argv)
    if not args.run_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Use a simple run ID")
    code = code_manifest()
    if args.lock and code != json.loads(args.lock.read_text())["code_sha256"]:
        raise ValueError("Code or prompts changed after the fresh-task lock")
    store = Store()
    path = store.root / "runs" / args.run_id / "manifest.json"
    if path.exists():
        raise ValueError("Preserve existing runs; choose a new run ID")
    fixture = Path(fixtures[args.suite])
    fixture_directory = (
        (DATA / fixture.relative_to("data")) if fixture.parts[0] == "data" else PROJECT / fixture
    )
    pins = json.loads((fixture_directory / "manifest.json").read_text())
    if any(
        sha((fixture_directory / name).read_bytes()) != pins[name]
        for name in ["public.json", "private.json"]
    ):
        raise ValueError("Frozen fixture changed")
    tasks = json.loads((fixture_directory / "public.json").read_text())["tasks"]
    source_path = source("sources/manifest.json")
    sources = json.loads(source_path.read_text())
    indexes = {p: index_class(p) for p in sorted({t["provider"] for t in tasks})}
    evidence = evidence_class(indexes, tasks, args.arm)
    guides = load_guides()
    manifest = {
        "id": args.run_id,
        "arm": args.arm,
        "suite": args.suite,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "code_sha256": code,
        "fixtures": fixtures[args.suite],
        "fixture_manifest": pins,
        "sources": sources,
        "stages": {},
        "expansions": [],
        "private_feedback_to_model": False,
        "reuse_from": args.reuse_from,
        "backend": "Codex CLI default model; user config ignored; no tools/web/apps",
        "cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "status": "running",
    }
    manifest["code_snapshot"] = {
        name: store.put((PROJECT / name).read_text(), "text/plain") for name in code
    }
    manifest["pagination_guides"] = store.put(guides)
    write_json(path, manifest)
    discovery_schema, plan_schema = deepcopy(DISCOVERY), deepcopy(BATCH)
    for schema in [discovery_schema, plan_schema]:
        schema["properties"]["plans"].update(minItems=len(tasks), maxItems=len(tasks))
    discover_prompt = (ROOT / "discover.txt").read_text() + extra_instructions
    plan_prompt = (
        (ROOT / "plan.txt").read_text() + (ROOT / "plan-extra.txt").read_text() + extra_instructions
    )
    try:
        discovery = infer(
            evidence.discovery_payload(sources),
            "discovery",
            discovery_schema,
            discover_prompt,
            path,
            manifest,
            store,
        )
        evidence.select(discovery)
        batch = infer(
            evidence.plan_payload(sources, guides, discovery),
            "compile-0",
            plan_schema,
            plan_prompt,
            path,
            manifest,
            store,
        )
        if args.arm == "adaptive":
            for iteration in [1, 2]:
                expansion = evidence.expand(batch, iteration)
                manifest["expansions"].append(store.put(expansion))
                if not expansion["events"]:
                    break
                if expansion["fallback_used"] and iteration == 2:
                    payload = evidence.discovery_payload(sources)
                    payload.update(
                        previous_plan=batch,
                        public_validation_feedback=expansion["public_validation_feedback"],
                    )
                    discovery = infer(
                        payload,
                        "fallback-discovery",
                        discovery_schema,
                        discover_prompt,
                        path,
                        manifest,
                        store,
                    )
                    evidence.select(discovery)
                batch = infer(
                    evidence.plan_payload(
                        sources, guides, discovery, previous=batch, expansion=expansion
                    ),
                    "compile-" + str(iteration),
                    plan_schema,
                    plan_prompt,
                    path,
                    manifest,
                    store,
                )
        manifest["plans"] = store.put(batch)
        for plan in batch["plans"]:
            task = next(task for task in tasks if task["id"] == plan["task_id"])
            admit(plan, task, indexes)
        manifest["admission_status"] = "accepted"
        manifest["evidence_cards"] = store.put(evidence.cards)
        manifest["evidence_reads"] = store.put(list(evidence.reads.values()))
        manifest["candidate_counts"] = {p: len(ops) for p, ops in evidence.available.items()}
        manifest["status"] = "completed"
    except Exception as exc:
        manifest["admission_status"] = "rejected"
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        raise
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(path, manifest)
    print("Saved compressed run", args.run_id, flush=True)


if __name__ == "__main__":
    main()
