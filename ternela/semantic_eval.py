# SPDX-License-Identifier: Apache-2.0
"""Evaluate optional Jev judgments on a frozen, explicitly labeled development corpus."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path

from ternela.storage import PROJECT, Store, sha, strict_json, write_json

from .semantic_review import CRITERIA, DIMENSIONS, review_plan, review_request
from .transport import decode_body


def load_cases():
    root = PROJECT / "evals"
    manifest = strict_json((root / "semantic_cases.manifest.json").read_bytes())
    packed = (root / "semantic_cases.json.gz").read_bytes()
    raw = decode_body(packed, "gzip", 1024 * 1024)
    if sha(packed) != manifest["gzip_sha256"] or sha(raw) != manifest["raw_sha256"]:
        raise ValueError("Semantic corpus hash mismatch")
    corpus = strict_json(raw)
    cases = corpus["cases"]
    identities = [case["id"] for case in cases]
    if len(cases) != manifest["cases"] or len(identities) != len(set(identities)):
        raise ValueError("Invalid semantic corpus identities or count")
    for case in cases:
        if not case["expected"] or set(case["expected"]) - set(DIMENSIONS):
            raise ValueError("Semantic corpus has unknown or missing judgment labels")
        if any(value not in CRITERIA for value in case["expected"].values()):
            raise ValueError("Semantic corpus has an invalid label")
        review_request(case["task"], case["plan"])
    return corpus, manifest


def summarize(records):
    """Score only labeled dimensions; never count service failures as correct abstentions."""
    matrix = {expected: {actual: 0 for actual in CRITERIA} for expected in CRITERIA}
    failed, correct = 0, 0
    for record in records:
        if record.get("error"):
            failed += 1
            continue
        for key, expected in record["expected"].items():
            actual = record["review"]["answers"][key]["choice"]
            matrix[expected][actual] += 1
            correct += actual == expected
    total = sum(sum(row.values()) for row in matrix.values())
    return {
        "judgments_scored": total,
        "correct": correct,
        "confusion": matrix,
        "false_matches": matrix["mismatch"]["matches"] + matrix["unclear"]["matches"],
        "unclear_answers": sum(row["unclear"] for row in matrix.values()),
        "service_failures": failed,
    }


def evaluate(*, live=False, model="jev-latest"):
    corpus, manifest = load_cases()
    result = {
        "status": "not_run",
        "corpus_sha256": manifest["raw_sha256"],
        "case_count": len(corpus["cases"]),
        "requested_model": model,
        "scope": corpus["scope"],
        "records": [],
    }
    if not live:
        result["reason"] = "offline_validation_only"
        return result
    if not os.getenv("TYPESAFE_API_KEY"):
        result["reason"] = "missing_credentials"
        return result
    for case in corpus["cases"]:
        record = {"id": case["id"], "expected": case["expected"]}
        started = time.perf_counter()
        try:
            record["review"] = review_plan(case["task"], case["plan"], model=model)
        except (ValueError, RuntimeError, OSError):
            record["error"] = "review_failed"
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        result["records"].append(record)
    result["summary"] = summarize(result["records"])
    result["status"] = (
        "completed_with_failures" if result["summary"]["service_failures"] else "completed"
    )
    return result


def save_report(result, directory):
    """Large answers go to verified gzip blobs; the manifest keeps scores and references."""
    directory = Path(directory)
    store = Store(directory)
    report = dict(
        result,
        records=[{"id": record["id"], "ref": store.put(record)} for record in result["records"]],
    )
    write_json(directory / "report.json", report)
    return directory / "report.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send the frozen public development fixtures to TypeSafe; consumes API usage",
    )
    parser.add_argument("--model", default="jev-latest")
    args = parser.parse_args()
    result = evaluate(live=args.live, model=args.model)
    path = save_report(result, PROJECT / "data/audits/semantic-eval" / uuid.uuid4().hex)
    print(json.dumps({key: value for key, value in result.items() if key != "records"}, indent=2))
    print("Saved:", path)
    if result["status"] != "completed" and args.live:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
