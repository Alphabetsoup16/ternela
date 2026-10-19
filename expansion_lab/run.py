# SPDX-License-Identifier: Apache-2.0
"""Run fixed/full/adaptive evidence compilation with compressed experiment records."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from docs_lab.v2.schema import DISCOVERY
from .schema import BATCH
from .evidence import Evidence
from .inference import infer
from .source import SourceIndex
from .storage import PROJECT, Store, sha, write_json

ROOT = Path(__file__).parent
FIXTURES = {"development": "docs_lab/fixtures", "previous_fresh": "docs_lab/v2/fresh", "new_fresh": "expansion_lab/fresh"}


def code_manifest():
    files = list(ROOT.glob("*.py")) + list(ROOT.glob("*.txt")) + list(ROOT.glob("*.json"))
    files += list((PROJECT / "docs_lab/v2").glob("*.py")) + [PROJECT / "docs_lab/v2/plan.txt", PROJECT / "docs_lab/v2/guides.json", PROJECT / "docs_lab/source.py", PROJECT / "docs_lab/runtime.py", PROJECT / "docs_lab/audit.py"]
    files += [PROJECT / "docs_lab/schema.py", PROJECT / "observation_lab/schema.py"] + list((PROJECT / "tests").glob("*.py"))
    return {str(p.relative_to(PROJECT)): sha(p.read_bytes()) for p in sorted(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["fixed", "adaptive", "full"], required=True)
    parser.add_argument("--suite", choices=list(FIXTURES), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--reuse-from", help="Reuse only completed stages whose input, prompt and schema hashes match exactly")
    args = parser.parse_args()
    if not args.run_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Use a simple run ID")
    code = code_manifest()
    if args.lock and code != json.loads(args.lock.read_text())["code_sha256"]:
        raise ValueError("Code or prompts changed after the fresh-task lock")
    store = Store()
    path = store.root / "runs" / args.run_id / "manifest.json"
    if path.exists():
        raise ValueError("Preserve existing runs; choose a new run ID")
    fixtures = PROJECT / FIXTURES[args.suite]
    pins = json.loads((fixtures / "manifest.json").read_text())
    if any(sha((fixtures / name).read_bytes()) != pins[name] for name in ["public.json", "private.json"]):
        raise ValueError("Frozen fixture changed")
    tasks = json.loads((fixtures / "public.json").read_text())["tasks"]
    source_path = PROJECT / "data/sources/manifest.json"
    if not source_path.exists():
        source_path = PROJECT / "docs_lab/sources/manifest.json"
    sources = json.loads(source_path.read_text())
    indexes = {p: SourceIndex(p) for p in sorted({t["provider"] for t in tasks})}
    evidence = Evidence(indexes, tasks, args.arm)
    guides = json.loads((PROJECT / "docs_lab/v2/guides.json").read_text())
    manifest = {"id": args.run_id, "arm": args.arm, "suite": args.suite, "started_at": datetime.now(timezone.utc).isoformat(),
                "code_sha256": code, "fixtures": FIXTURES[args.suite], "fixture_manifest": pins, "sources": sources,
                "stages": {}, "expansions": [], "private_feedback_to_model": False,
                "reuse_from": args.reuse_from,
                "backend": "Codex CLI default model; user config ignored; no tools/web/apps", "cli_version": subprocess.check_output(["codex", "--version"], text=True).strip(), "status": "running"}
    manifest["code_snapshot"] = {name: store.put((PROJECT / name).read_text(), "text/plain") for name in code}
    write_json(path, manifest)
    discovery_schema, plan_schema = deepcopy(DISCOVERY), deepcopy(BATCH)
    for schema in [discovery_schema, plan_schema]:
        schema["properties"]["plans"].update(minItems=len(tasks), maxItems=len(tasks))
    discover_prompt = (ROOT / "discover.txt").read_text()
    plan_prompt = (PROJECT / "docs_lab/v2/plan.txt").read_text() + (ROOT / "plan-extra.txt").read_text()
    try:
        discovery = infer(evidence.discovery_payload(sources), "discovery", discovery_schema, discover_prompt, path, manifest, store)
        evidence.select(discovery)
        batch = infer(evidence.plan_payload(sources, guides, discovery), "compile-0", plan_schema, plan_prompt, path, manifest, store)
        if args.arm == "adaptive":
            for iteration in [1, 2]:
                expansion = evidence.expand(batch, iteration)
                manifest["expansions"].append(store.put(expansion))
                if not expansion["events"]:
                    break
                if expansion["fallback_used"] and iteration == 2:
                    payload = evidence.discovery_payload(sources)
                    payload.update(previous_plan=batch, public_validation_feedback=expansion["public_validation_feedback"])
                    discovery = infer(payload, "fallback-discovery", discovery_schema, discover_prompt, path, manifest, store)
                    evidence.select(discovery)
                batch = infer(evidence.plan_payload(sources, guides, discovery, previous=batch, expansion=expansion), "compile-" + str(iteration), plan_schema, plan_prompt, path, manifest, store)
        manifest["plans"] = store.put(batch)
        manifest["evidence_cards"] = store.put(evidence.cards)
        manifest["evidence_reads"] = store.put(list(evidence.reads.values()))
        manifest["candidate_counts"] = {p: len(ops) for p, ops in evidence.available.items()}
        manifest["status"] = "completed"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        raise
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(path, manifest)
    print("Saved compressed run", args.run_id, flush=True)


if __name__ == "__main__":
    main()
