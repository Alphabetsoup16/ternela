# SPDX-License-Identifier: Apache-2.0
"""Two-stage document discovery/planning with full directories and targeted schemas."""

import argparse
import copy
import hashlib
import json
import shutil
from pathlib import Path

from docs_lab.run import infer
from docs_lab.source import Index, ROOT as SOURCE_ROOT
from .retrieval import branch, card, directory, root_names
from .schema import BATCH, DISCOVERY

ROOT = Path(__file__).parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_manifest():
    paths = list(ROOT.glob("*.py")) + list(ROOT.glob("*.txt")) + [SOURCE_ROOT / "run.py", SOURCE_ROOT / "source.py", SOURCE_ROOT / "runtime.py", SOURCE_ROOT / "audit.py"]
    return {str(p.relative_to(SOURCE_ROOT)): digest(p) for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("development", "fresh"), default="development")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lock", type=Path, help="Require identical planner/runtime code to an existing pre-fresh lock")
    parser.add_argument("--discovery-from",type=Path,help="Reuse a completed discovery on identical public tasks")
    args = parser.parse_args()
    fixture_dir = ROOT / "fresh" if args.suite == "fresh" else SOURCE_ROOT / "fixtures"
    public = json.loads((fixture_dir / "public.json").read_text())
    hashes = code_manifest()
    if args.lock:
        lock = json.loads(args.lock.read_text())
        if hashes != lock["code_sha256"] or digest(ROOT / "guides.json") != lock["guides_sha256"]:
            raise ValueError("Code/prompt/guide snapshot changed after fresh-run lock")
    if args.output.exists():
        raise ValueError("Use a new output directory")
    args.output.mkdir(parents=True)
    source_manifest = json.loads((SOURCE_ROOT / "sources/manifest.json").read_text())
    for source in source_manifest:
        if digest(SOURCE_ROOT / "sources" / f"{source['provider']}.json") != source["sha256"]:
            raise ValueError("Pinned API source snapshot changed")
    fixture_manifest = json.loads((fixture_dir / "manifest.json").read_text())
    if digest(fixture_dir / "public.json") != fixture_manifest["public.json"]:
        raise ValueError("Public task fixture changed")
    indexes = {p: Index(p) for p in {t["provider"] for t in public["tasks"]}}
    hints, cards = {}, {}
    for task in public["tasks"]:
        index = indexes[task["provider"]]
        hits = index.search(task["task"], 8)
        hints[task["id"]] = hits
        for hit in hits[:3]:
            key = task["provider"] + "::" + hit["operation"]
            if key not in cards:
                cards[key] = root_names(index, hit["operation"])
    metadata = {"suite": args.suite, "fixture_directory": str(fixture_dir), "fixture_manifest": fixture_manifest,
                "sources": source_manifest, "code_sha256": hashes, "guides_sha256": digest(ROOT / "guides.json"),
                "retrieval": "Complete provider operation directory; model selects operations and inline schema branches. BM25 hints never exclude operations.",
                "hand_authored_catalogs": False, "private_feedback_to_model": False,
                "planner_lock": str(args.lock) if args.lock else None}
    metadata["discovery_reused_from"] = str(args.discovery_from) if args.discovery_from else None
    (args.output / "experiment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    payload = {"round": 1, "tasks": public["tasks"], "source_info": source_manifest,
               "complete_operation_directories": {p: directory(i) for p, i in indexes.items()},
               "search_hints": hints, "initial_schema_cards": cards}
    discovery_schema = copy.deepcopy(DISCOVERY)
    discovery_schema["properties"]["plans"].update(minItems=len(public["tasks"]), maxItems=len(public["tasks"]))
    if args.discovery_from:
        old_input=json.loads((args.discovery_from/"round-1/input.json").read_text())
        old_meta=json.loads((args.discovery_from/"round-1/metadata.json").read_text())
        if old_input["tasks"]!=public["tasks"] or old_meta["status"]!="completed":
            raise ValueError("Cached discovery is not a successful run on identical tasks")
        shutil.copytree(args.discovery_from/"round-1",args.output/"round-1")
        discovery=json.loads((args.output/"round-1/plans.json").read_text())
    else:
        discovery = infer(payload, args.output / "round-1", schema=discovery_schema, prompt_path=ROOT / "discover.txt")
    tasks = {t["id"]: t for t in public["tasks"]}
    selected_cards, branches = {}, []
    for selection in discovery["plans"]:
        index = indexes[tasks[selection["task_id"]]["provider"]]
        for operation in selection["operations"]:
            key = index.provider + "::" + operation
            selected_cards[key] = card(index, operation, depth=1) if operation in index.by_key else {"error": "Operation not in official directory"}
        for read in selection["schema_reads"]:
            try:
                result = branch(index, read["operation"], read["section"], read["pointer"])
            except (KeyError, ValueError, TypeError, IndexError):
                result = {"found": False, "error": "Requested operation/schema path not found"}
            branches.append({"task_id": selection["task_id"], "requested": read, "result": result})
    guides = json.loads((ROOT / "guides.json").read_text())
    payload = {"round": 2, "tasks": public["tasks"], "source_info": source_manifest,
               "selected_schema_cards": selected_cards, "targeted_schema_reads": branches,
               "official_pagination_guides": guides, "discovery": discovery}
    plan_schema = copy.deepcopy(BATCH)
    plan_schema["properties"]["plans"].update(minItems=len(public["tasks"]), maxItems=len(public["tasks"]))
    plans = infer(payload, args.output / "round-2", schema=plan_schema, prompt_path=ROOT / "plan.txt")
    (args.output / "plans.json").write_text(json.dumps(plans, indent=2) + "\n")
    print("Saved", args.output / "plans.json", flush=True)


if __name__ == "__main__":
    main()
