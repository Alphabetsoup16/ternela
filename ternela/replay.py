# SPDX-License-Identifier: Apache-2.0
"""Bounded local receipt examples; offline behavioral comparison, never a truth oracle."""

import gzip
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from pathlib import Path

from ternela import connectors
from ternela.contracts import read_record
from ternela.observations import run_observations
from ternela.storage import Store, atomic_write, canonical, sha, strict_json
from ternela.validation import admit

MAX_CASES, MAX_PER_PROVIDER = 64, 12
MAX_CASE_BYTES, MAX_CORPUS_BYTES, MAX_SCAN = 262144, 4194304, 256


def shape(value, depth=0):
    """Retain types and useful boundaries without indexing arbitrary payload values."""
    if depth >= 8:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [
            min(len(value), 2),
            sorted({canonical(shape(v, depth + 1)).decode() for v in value}),
        ]
    if type(value) is int:
        return (
            "integer:zero"
            if value == 0
            else "integer:large"
            if abs(value) > 2**53
            else "integer:negative"
            if value < 0
            else "integer:positive"
        )
    if isinstance(value, str):
        return "string:present" if value else "string:empty"
    return type(value).__name__


def signature(result, observations):
    return {
        "status": result["status"],
        "checks": sorted((c["id"], c["outcome"]) for c in result.get("checks", [])),
        "guards": sorted(g["id"] for g in result.get("preservation_failures", [])),
        "observations": {
            key: {
                field: value.get(field)
                for field in ("http_status", "collection_complete", "traversal_reason")
            }
            for key, value in sorted(observations.items())
        },
    }


def example(task, plan, receipt):
    if receipt["result"].get("stale"):
        raise ValueError("freshness-only receipt")
    task = {k: task[k] for k in ("id", "provider", "task", "context")}
    live = receipt["mode"].startswith("live public ")
    snapshots = deepcopy(receipt["input"])
    calls = {
        key: [row["request"] for row in value.get("trace", [])]
        for key, value in receipt["observations"].items()
    }
    for key, pages in snapshots.items():
        pages = [pages] if isinstance(pages, dict) else pages
        snapshots[key] = pages
        calls.setdefault(key, [])
        if live and len(calls[key]) != len(pages):
            raise ValueError("incomplete request trace")
        if live:
            connector = connectors.CONNECTORS.get(task["provider"])
            for page in pages:
                if not page.get("url"):
                    raise ValueError("public read has no recorded URL")
                if (
                    connector.contract() is not None
                    and 200 <= page.get("source_http_status", page["http_status"]) < 300
                    and "source_body" not in page
                ):
                    raise ValueError("normalized read has no raw source payload")
    if not live:
        for key, trace in calls.items():
            pages = snapshots.setdefault(key, [])
            pages.extend(
                {"http_status": 0, "body": None} for _ in range(max(0, len(trace) - len(pages)))
            )
    expected = signature(receipt["result"], receipt["observations"])
    case = dict(
        task=task,
        plan=plan,
        phase=receipt["phase"],
        live=live,
        snapshots=snapshots,
        calls=calls,
        expected=expected,
    )
    case["id"] = sha(canonical([task, plan, receipt["phase"], live, expected, shape(snapshots)]))
    # JSON round-trip gives signatures the same list/tuple representation on every run.
    return strict_json(canonical(case))


def retain(cases):
    groups = defaultdict(list)
    for case in cases:
        groups[(case["task"]["provider"], case["expected"]["status"])].append(case)
    for group in groups.values():
        group.sort(key=lambda c: c["id"])
    selected, counts, size = [], Counter(), 0
    for index in range(MAX_PER_PROVIDER):
        for key in sorted(groups):
            if index >= len(groups[key]) or counts[key[0]] >= MAX_PER_PROVIDER:
                continue
            case = groups[key][index]
            length = len(canonical(case))
            if (
                len(selected) >= MAX_CASES
                or length > MAX_CASE_BYTES
                or size + length > MAX_CORPUS_BYTES - 4096
            ):
                continue
            selected.append(case)
            counts[key[0]] += 1
            size += length
    return selected


def load_corpus(data):
    path = Path(data) / "checks/receipts.json.gz"
    if not path.exists():
        return {"format": 1, "cursor": None, "cases": []}
    with gzip.open(path, "rb") as stream:
        raw = stream.read(MAX_CORPUS_BYTES + 1)
    if len(raw) > MAX_CORPUS_BYTES:
        raise ValueError("Receipt corpus exceeds read limit")
    corpus = strict_json(raw)
    if (
        not isinstance(corpus, dict)
        or type(corpus.get("format")) is not int
        or corpus["format"] != 1
        or not isinstance(corpus.get("cases"), list)
        or len(corpus["cases"]) > MAX_CASES
    ):
        raise ValueError("Unsupported receipt corpus")
    return corpus


def harvest(data, corpus=None):
    """Incrementally read accepted events, leaving the application's DB and blobs alone."""
    data = Path(data)
    path = data / "checks/receipts.json.gz"
    corpus = load_corpus(data) if corpus is None else corpus
    cases = {case["id"]: case for case in corpus["cases"]}
    report = dict(
        scanned=0,
        added=0,
        duplicates=0,
        skipped={},
        retained=len(cases),
        evicted=0,
        excluded_total=corpus.get("excluded", {}),
    )
    db_path = data / "workbench/watches.sqlite3"
    if not db_path.exists():
        return list(cases.values()), report
    stat = db_path.stat()
    identity = [stat.st_dev, stat.st_ino]
    cursor = corpus.get("cursor") or {}
    after = cursor.get("rowid", 0) if cursor.get("database") == identity else 0
    store, skipped = Store(data), Counter()
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        maximum = db.execute("SELECT coalesce(max(rowid),0) FROM events").fetchone()[0]
        if after > maximum:
            after = 0
        rows = db.execute(
            "SELECT e.rowid AS position,e.accepted,e.receipt_ref,w.task_ref,w.plan_ref FROM events e LEFT JOIN watches w ON w.id=e.watch_id WHERE e.rowid>? ORDER BY e.rowid LIMIT ?",
            (after, MAX_SCAN),
        ).fetchall()
        for row in rows:
            report["scanned"] += 1
            after = row["position"]
            if not row["accepted"] or not row["receipt_ref"]:
                continue
            try:
                task = store.get(strict_json(row["task_ref"]), max_bytes=MAX_CASE_BYTES)
                plan = read_record(
                    store, strict_json(row["plan_ref"]), "plan", max_bytes=MAX_CASE_BYTES
                )
                receipt = read_record(
                    store, strict_json(row["receipt_ref"]), "receipt", max_bytes=MAX_CASE_BYTES
                )
                case = example(task, plan, receipt)
                if len(canonical(case)) > MAX_CASE_BYTES:
                    raise ValueError("example exceeds retention limit")
                if case["id"] in cases:
                    report["duplicates"] += 1
                else:
                    cases[case["id"]] = case
                    report["added"] += 1
            except (ValueError, OSError, EOFError, KeyError, TypeError, IndexError) as exc:
                category = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    and str(exc)
                    in {
                        "freshness-only receipt",
                        "incomplete request trace",
                        "public read has no recorded URL",
                        "normalized read has no raw source payload",
                        "example exceeds retention limit",
                        "Blob exceeds read limit",
                        "Corrupt content-addressed blob",
                    }
                    else "missing blob"
                    if isinstance(exc, FileNotFoundError)
                    else "unreadable or incomplete receipt"
                )
                skipped[category] += 1
    selected = retain(cases.values())
    report.update(
        retained=len(selected),
        evicted=len(cases) - len(selected),
        skipped=dict(skipped),
        pending=maximum > after,
    )
    excluded = Counter(corpus.get("excluded", {})) + skipped
    report["excluded_total"] = dict(excluded)
    updated = dict(
        format=1,
        cursor=dict(database=identity, rowid=after),
        cases=selected,
        excluded=dict(excluded),
    )
    raw = canonical(updated)
    if len(raw) > MAX_CORPUS_BYTES:
        raise ValueError("Receipt corpus exceeds write limit")
    if updated != corpus:
        atomic_write(path, gzip.compress(raw, compresslevel=9, mtime=0))
    return selected, report


def replay(case, indexes=None):
    """Reuse acquisition and evaluation; the only reader returns recorded provider JSON."""
    task, plan = case["task"], case["plan"]
    admit(plan, task, indexes)
    consumed, issues = Counter(), []

    def factory(operation):
        key = operation["id"]

        def transport(call):
            offset = consumed[key]
            consumed[key] += 1
            try:
                if call != case["calls"][key][offset]:
                    raise ValueError("request bindings or continuation changed")
                saved = case["snapshots"][key][offset]
                if not case["live"]:
                    return deepcopy(saved)

                def reader(url, host, timeout):
                    if url != saved["url"]:
                        raise ValueError("connector destination changed")
                    raw = {
                        k: deepcopy(v)
                        for k, v in saved.items()
                        if k not in ("view_contract", "source_body", "source_http_status", "error")
                    }
                    raw["body"] = deepcopy(saved.get("source_body", saved.get("body")))
                    raw["http_status"] = saved.get("source_http_status", saved["http_status"])
                    return raw

                return connectors.request(task["provider"], call, reader=reader)
            except (KeyError, IndexError, ValueError) as exc:
                issues.append(f"{key}: {exc}")
                raise ValueError("Recorded read no longer matches the current request") from exc

        return transport

    result, observed = run_observations(plan, task["context"], case["phase"], factory)
    for key, pages in case["snapshots"].items():
        if consumed[key] != len(case["calls"].get(key, [])):
            issues.append(f"{key}: recorded pages were not consumed exactly")
    actual = strict_json(canonical(signature(result, observed)))
    return {
        "id": case["id"],
        "provider": task["provider"],
        "passed": not issues and actual == case["expected"],
        "expected": case["expected"],
        "actual": actual,
        "issues": issues,
    }


def compare(cases):
    failures, indexes = [], {}
    for case in cases:
        try:
            provider = case["task"]["provider"]
            if provider not in indexes:
                indexes[provider] = connectors.source_index(provider)
            result = replay(case, indexes)
            if not result["passed"]:
                failures.append(result)
        except (ValueError, OSError, EOFError, KeyError, TypeError, IndexError) as exc:
            failures.append(dict(id=case.get("id"), passed=False, error=str(exc)))
    return dict(total=len(cases), passed=len(cases) - len(failures), failures=failures)
