# SPDX-License-Identifier: Apache-2.0
"""Controlled local export observations, using the existing watch/evidence runtime."""

import csv
import io
import json
import time

from ternela.schema import obj
from ternela.specification import Index
from ternela.storage import Store, canonical, sha, write_json
from ternela.watches import Watches

PROVIDER = "demo_local_export"
OPERATION = "GET /jobs/{job_id}"
CONSUMER = "sales-summary-agent"
ROWS = (("order-1", 4200, "USD"), ("order-2", 1900, "USD"), ("order-3", 700, "USD"))


def configuration(root):
    record = json.loads((root / "demo.json").read_text())
    if record.get("format") != 1 or record.get("agent") not in {"reference", "codex"}:
        raise ValueError("Not a supported agent-demo folder")
    return record


def engine(root):
    return Watches(root / "data/watches.sqlite3", Store(root / "data"))


def recipe(job_id):
    schema = obj(
        {
            "job_id": {"type": "string"},
            "status": {"type": "string", "enum": ["running", "completed", "failed"]},
            "exit_code": {"type": "integer"},
            "artifact": {"type": "string"},
            "sha256": {"type": "string"},
            "rows": {"type": "integer"},
        }
    )
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/jobs/{job_id}": {
                "get": {
                    "summary": "Caller-observed local export process and its actual CSV bytes",
                    "parameters": [
                        {
                            "name": "job_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"content": {"application/json": {"schema": schema}}}},
                }
            }
        },
    }
    index = Index(PROVIDER, spec)
    index.provenance = {
        "provider": PROVIDER,
        "kind": "demo_local_observation_contract",
        "sha256": sha(canonical(spec)),
        "limitation": "Local producer observations; no public API, independent attestation or production filesystem connector.",
    }
    task = {
        "id": "summarize-" + job_id,
        "provider": PROVIDER,
        "task": "After this exact local export completes successfully, summarize sales.csv.",
        "context": {"job_id": job_id},
    }
    checks = []
    for field, op, value in (
        ("job_id", "eq", "$context.job_id"),
        ("status", "eq", "completed"),
        ("exit_code", "eq", 0),
        ("artifact", "eq", "sales.csv"),
        ("sha256", "ne", ""),
        ("rows", "eq", len(ROWS)),
    ):
        checks.append(
            dict(
                id=field,
                request_id="export",
                phase="after",
                collection_pointer="",
                selector_json="{}",
                select_mode="single",
                order_by="",
                field_pointer="/" + field,
                op=op,
                value_json=json.dumps(value),
                reason="Require the exact export's " + field,
            )
        )
    plan = dict(
        task_id=task["id"],
        requests=[
            dict(
                id="export",
                operation=OPERATION,
                bindings_json=json.dumps({"path": {"job_id": "$context.job_id"}}),
                purpose="Read a caller-supplied local job observation; no HTTP request is sent.",
                auth_notes="Owned demo folder only; no credential or public connector.",
                poll_interval_seconds=1,
                pagination=dict(
                    mode="none",
                    items_pointer="",
                    cursor_parameter="",
                    next_cursor_pointer="",
                    has_more_pointer="",
                    cursor_item_pointer="",
                    max_pages=1,
                ),
            )
        ],
        checks=checks,
        requirements=[
            dict(
                id="export_complete",
                phase="after",
                text=task["task"],
                coverage="observable",
                check_ids=[c["id"] for c in checks],
                limitation=index.provenance["limitation"],
            )
        ],
        followup_queries=[],
        schema_refs=[],
        limitations=[index.provenance["limitation"]],
        preservations=[],
        retrieval_needs=[],
    )
    return task, plan, index


def create_watch(root):
    marker = root / "watch.json"
    if marker.exists():
        return engine(root).get(json.loads(marker.read_text())["id"])
    task, plan, index = recipe(configuration(root)["job_id"])
    watches = engine(root)
    watch = watches.create(task, plan, ttl=300, indexes={PROVIDER: index})
    write_json(marker, {"id": watch["id"]})
    publish(root, "running", -1)
    return watches.get(watch["id"])


def watch_id(root):
    return json.loads((root / "watch.json").read_text())["id"]


def sales(root):
    path = root / "sales.csv"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Export must be a bounded regular CSV file")
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode()))
    if reader.fieldnames != ["order_id", "net_cents", "currency"]:
        raise ValueError("Unexpected export columns")
    rows = list(reader)
    if len(rows) != len(ROWS) or len({r["order_id"] for r in rows}) != len(rows):
        raise ValueError("Incomplete or duplicate export rows")
    if any(
        None in r
        or not r["order_id"]
        or r["currency"] != "USD"
        or not isinstance(r["net_cents"], str)
        or not r["net_cents"].isdigit()
        for r in rows
    ):
        raise ValueError("Invalid export values")
    return raw, rows


def publish(root, status, exit_code):
    config = configuration(root)
    body = dict(
        job_id=config["job_id"],
        status=status,
        exit_code=exit_code,
        artifact="sales.csv",
        sha256="",
        rows=0,
    )
    if status == "completed":
        raw, rows = sales(root)
        body.update(sha256=sha(raw), rows=len(rows))
    watches = engine(root)
    return watches.submit(
        watch_id(root),
        "export-" + status,
        1 if status == "running" else 2,
        {"export": [{"http_status": 200, "headers": {}, "body": body}]},
        mode="real local export; caller-supplied process/file observation",
    )


def fresh_completion(root, message):
    watches = engine(root)
    current = watches.get(watch_id(root))["receipt"]
    if not current or current["result"]["status"] != "complete":
        raise ValueError("The current export condition is not complete")
    if current["result"].get("stale") or current["expires_at"] <= time.time():
        raise ValueError("Export evidence is expired")
    if message["receipt"]["result"]["status"] != "complete":
        raise ValueError("A pending historical decision cannot resume the agent")
    raw, rows = sales(root)
    observed = current["input"]["export"][0]["body"]
    if observed["job_id"] != configuration(root)["job_id"] or observed["sha256"] != sha(raw):
        raise ValueError("The artifact no longer matches the observed export")
    return dict(
        job_id=observed["job_id"],
        orders=len(rows),
        total_cents=sum(int(r["net_cents"]) for r in rows),
        currency="USD",
        artifact_sha256=sha(raw),
        delivery_key=f"{watch_id(root)}:{message['id']}",
    )


def next_delivery(root):
    messages = engine(root).deliveries(watch_id(root), CONSUMER)
    return messages[0] if messages else None


def inspect(root):
    message = next_delivery(root)
    if not message:
        raise ValueError("No pending agent delivery")
    expected = fresh_completion(root, message)
    return dict(
        watch_id=watch_id(root),
        delivery_key=expected["delivery_key"],
        artifact="sales.csv",
        artifact_sha256=expected["artifact_sha256"],
        required_answer_fields=list(expected),
        job_id=expected["job_id"],
    )


def verify_answer(root, message):
    expected = fresh_completion(root, message)
    path = root / "answer.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        raise ValueError("Agent did not write a bounded answer.json")
    actual = json.loads(path.read_text())
    if actual != expected or any(type(actual[k]) is not int for k in ("orders", "total_cents")):
        raise ValueError("Agent answer does not match the actual export and delivery")
    return actual


def reference_finish(root):
    message = next_delivery(root)
    if not message:
        raise ValueError("No pending delivery")
    write_json(root / "answer.json", fresh_completion(root, message))
