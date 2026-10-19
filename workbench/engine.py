# SPDX-License-Identifier: Apache-2.0
"""Persistent, event-fed watches with immutable receipts and a transactional outbox.

This is a single-host prototype. Events are complete observation snapshots supplied
by an authenticated caller, not verified webhooks or independently ordered facts.
"""

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from docs_lab.v2.runtime import check_details, evaluate, execute
from expansion_lab.runtime import enforce_guard
from expansion_lab.storage import Store
from source_lab.evidence import SourceIndex
from ternela.validation import admit, expression_values


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def phase_plan(plan, phase):
    """Observe only the requested phase and its transitive data dependencies."""
    result = deepcopy(plan)
    result["checks"] = [c for c in plan["checks"] if c["phase"] == phase]
    result["requirements"] = [r for r in plan["requirements"] if r["phase"] == phase]
    needed = {c["request_id"] for c in result["checks"]}
    by_id = {r["id"]: r for r in plan["requests"]}
    for _ in range(len(by_id) + 1):
        old = set(needed)
        for guard in plan["preservations"]:
            if guard["target_request_id"] in needed:
                needed.add(guard["source_request_id"])
        for identity in list(needed):
            if identity not in by_id:
                raise ValueError("Missing request dependency")
            bindings = json.loads(by_id[identity]["bindings_json"])
            needed.update(
                value[len("$response.") :].partition("/")[0]
                for value in expression_values(bindings)
                if value.startswith("$response.")
            )
        if needed == old:
            break
    result["requests"] = [r for r in plan["requests"] if r["id"] in needed]
    result["preservations"] = [g for g in plan["preservations"] if g["target_request_id"] in needed]
    return result


def observe(plan, context, snapshots, phase):
    selected = phase_plan(plan, phase)
    observed, failures = {}, []
    for request in selected["requests"]:
        try:
            for guard in selected["preservations"]:
                if guard["target_request_id"] == request["id"]:
                    try:
                        enforce_guard(guard, request, context, observed)
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        failures.append({"id": guard["id"], "reason": str(exc)})
                        raise
            pages = snapshots.get(request["id"], [])
            if isinstance(pages, dict):
                pages = [pages]
            cursor = iter(pages)

            def transport(call):
                return deepcopy(next(cursor, {"http_status": 0, "body": None}))

            observed[request["id"]] = execute(request, context, transport, observed)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            observed[request["id"]] = {
                "http_status": 0,
                "body": None,
                "collection_complete": False,
                "traversal_reason": str(exc),
                "trace": [],
            }
    result = evaluate(selected, observed, context, phase)
    if failures:
        result.update(status="unknown", preservation_failures=failures)
    return result, observed


class Watches:
    def __init__(self, path, store=None, clock=time.time):
        self.path = str(path)
        self.store = store or Store()
        self.clock = clock
        self._indexes = {}
        self._index_lock = threading.Lock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS watches(id TEXT PRIMARY KEY, task_ref TEXT, plan_ref TEXT,
                phase TEXT, ttl REAL, created REAL, revision INTEGER DEFAULT 0,
                receipt_ref TEXT, decision TEXT, updated REAL, expires REAL, stale INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS events(watch_id TEXT, event_id TEXT, fingerprint TEXT,
                revision INTEGER, accepted INTEGER, receipt_ref TEXT, PRIMARY KEY(watch_id,event_id));
              CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, watch_id TEXT,
                receipt_ref TEXT, created REAL, acked REAL);
            """
            )
            columns = {r[1] for r in db.execute("PRAGMA table_info(watches)")}
            for name, declaration in [("expires", "REAL"), ("stale", "INTEGER DEFAULT 0")]:
                if name not in columns:
                    db.execute(f"ALTER TABLE watches ADD COLUMN {name} {declaration}")
            for row in db.execute(
                "SELECT * FROM watches WHERE receipt_ref IS NOT NULL AND expires IS NULL"
            ).fetchall():
                receipt = self.store.get(json.loads(row["receipt_ref"]))
                db.execute(
                    "UPDATE watches SET expires=?,stale=? WHERE id=?",
                    (receipt["expires_at"], int(bool(receipt["result"].get("stale"))), row["id"]),
                )
            db.execute("CREATE INDEX IF NOT EXISTS watches_expiry ON watches(stale,expires)")
            db.execute("CREATE INDEX IF NOT EXISTS outbox_watch_cursor ON outbox(watch_id,id)")

    def now(self):
        value = self.clock()
        if (
            type(value) not in (int, float)
            or (type(value) is float and not math.isfinite(value))
            or value < 0
        ):
            raise ValueError("Clock must return a finite, nonnegative timestamp")
        return value

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def create(self, task, plan, phase="after", ttl=30, indexes=None):
        if phase not in ["before", "after"]:
            raise ValueError("Invalid phase")
        if (
            type(ttl) not in [int, float]
            or (type(ttl) is float and not math.isfinite(ttl))
            or not 1 <= ttl <= 86400
        ):
            raise ValueError("TTL must be 1–86400 seconds")
        if indexes is None:
            with self._index_lock:
                provider = task["provider"]
                if provider not in self._indexes:
                    self._indexes[provider] = SourceIndex(provider)
                indexes = self._indexes
        admission = admit(plan, task, indexes)
        task = dict(task, _admission=admission)
        phase_plan(plan, phase)
        identity = uuid.uuid4().hex
        now = self.now()
        task_ref, plan_ref = self.store.put(task), self.store.put(plan)
        with self.connect() as db:
            db.execute(
                "INSERT INTO watches(id,task_ref,plan_ref,phase,ttl,created,updated) VALUES(?,?,?,?,?,?,?)",
                (identity, json.dumps(task_ref), json.dumps(plan_ref), phase, ttl, now, now),
            )
        return self.get(identity)

    def _row(self, db, identity):
        row = db.execute("SELECT * FROM watches WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError("Watch not found")
        return row

    def _commit(self, db, row, receipt):
        ref = self.store.put(receipt)
        decision = digest(receipt["result"])
        db.execute(
            "UPDATE watches SET receipt_ref=?,decision=?,updated=?,expires=?,stale=? WHERE id=?",
            (
                json.dumps(ref),
                decision,
                self.now(),
                receipt["expires_at"],
                int(bool(receipt["result"].get("stale"))),
                row["id"],
            ),
        )
        if decision != row["decision"]:
            db.execute(
                "INSERT INTO outbox(watch_id,receipt_ref,created) VALUES(?,?,?)",
                (row["id"], json.dumps(ref), self.now()),
            )
        return ref

    def submit(
        self,
        identity,
        event_id,
        revision,
        snapshots,
        observed_at=None,
        mode="caller-supplied snapshot",
    ):
        # A duplicate may arrive after expiry or after a newer event. Never return
        # its historical success as the current decision.
        self.expire(identity)
        supplied_time = observed_at
        now = self.now()
        observed_at = now if observed_at is None else observed_at
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 160:
            raise ValueError("Invalid event ID")
        if type(revision) is not int or not 1 <= revision <= 2**63 - 1:
            raise ValueError("Revision must be an integer from 1 to 2^63-1")
        if (
            type(observed_at) not in [int, float]
            or (type(observed_at) is float and not math.isfinite(observed_at))
            or observed_at < 0
            or observed_at > now + 1
        ):
            raise ValueError("Invalid observation time")
        if not isinstance(snapshots, dict):
            raise ValueError("Snapshots must map request IDs to captured responses")
        fingerprint = digest(
            {
                "revision": revision,
                "snapshots": snapshots,
                "observed_at": supplied_time,
                "mode": mode,
            }
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.now()
            row = self._row(db, identity)
            if row["expires"] is not None and not row["stale"] and now >= row["expires"]:
                receipt = self.store.get(json.loads(row["receipt_ref"]))
                receipt["evaluated_at"] = now
                receipt["result"] = {
                    "status": "unknown",
                    "checks": [],
                    "gaps": [{"limitation": "Evidence expired; obtain a new observation"}],
                    "stale": True,
                }
                self._commit(db, row, receipt)
                row = self._row(db, identity)
            old = db.execute(
                "SELECT * FROM events WHERE watch_id=? AND event_id=?", (identity, event_id)
            ).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Event ID reused with different content")
                return {
                    "duplicate": True,
                    "accepted": bool(old["accepted"]),
                    "receipt": (
                        self.store.get(json.loads(row["receipt_ref"]))
                        if row["receipt_ref"]
                        else None
                    ),
                    "delivery_receipt": (
                        self.store.get(json.loads(old["receipt_ref"]))
                        if old["receipt_ref"]
                        else None
                    ),
                }
            # Caller revision and source time must both move forward; this is one ordered source lane.
            prior = self.store.get(json.loads(row["receipt_ref"])) if row["receipt_ref"] else None
            accepted = revision > row["revision"] and (
                prior is None or observed_at >= prior["observed_at"]
            )
            ref = None
            if accepted:
                task, plan = (
                    self.store.get(json.loads(row["task_ref"])),
                    self.store.get(json.loads(row["plan_ref"])),
                )
                result, observations = observe(plan, task["context"], snapshots, row["phase"])
                receipt = {
                    "watch_id": identity,
                    "event_id": event_id,
                    "revision": revision,
                    "observed_at": observed_at,
                    "evaluated_at": now,
                    "expires_at": observed_at + row["ttl"],
                    "phase": row["phase"],
                    "result": result,
                    "observations": observations,
                    "input": snapshots,
                    "plan_sha256": json.loads(row["plan_ref"])["sha256"],
                    "source_evidence": task["_admission"],
                    "mode": mode,
                }
                now = self.now()
                receipt["evaluated_at"] = now
                if now >= receipt["expires_at"]:
                    receipt["result"] = {
                        "status": "unknown",
                        "checks": [],
                        "gaps": [{"limitation": "Evidence expired; obtain a new observation"}],
                        "stale": True,
                    }
                ref = self._commit(db, row, receipt)
                db.execute("UPDATE watches SET revision=? WHERE id=?", (revision, identity))
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?)",
                (
                    identity,
                    event_id,
                    fingerprint,
                    revision,
                    int(accepted),
                    json.dumps(ref) if ref else None,
                ),
            )
        return {
            "duplicate": False,
            "accepted": accepted,
            "receipt": self.store.get(ref) if ref else None,
        }

    def expire(self, identity=None):
        with self.connect() as db:
            query = "SELECT * FROM watches WHERE stale=0 AND expires<=?"
            values = (self.now(),)
            if identity is not None:
                query += " AND id=?"
                values += (identity,)
            if db.execute(query + " LIMIT 1", values).fetchone() is None:
                return
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute(query, values).fetchall():
                receipt = self.store.get(json.loads(row["receipt_ref"]))
                if not receipt["result"].get("stale") and self.now() >= receipt["expires_at"]:
                    receipt = deepcopy(receipt)
                    receipt["evaluated_at"] = self.now()
                    receipt["result"] = {
                        "status": "unknown",
                        "checks": [],
                        "gaps": [{"limitation": "Evidence expired; obtain a new observation"}],
                        "stale": True,
                    }
                    self._commit(db, row, receipt)

    def get(self, identity):
        self.expire(identity)
        with self.connect() as db:
            row = self._row(db, identity)
        task = self.store.get(json.loads(row["task_ref"]))
        plan = self.store.get(json.loads(row["plan_ref"]))
        receipt = self.store.get(json.loads(row["receipt_ref"])) if row["receipt_ref"] else None
        selected = phase_plan(plan, row["phase"])
        return {
            "id": identity,
            "phase": row["phase"],
            "ttl": row["ttl"],
            "revision": row["revision"],
            "task": task,
            "plan": plan,
            "active_plan": selected,
            "check_evidence": {
                check["id"]: check_details(
                    check,
                    (receipt or {}).get("observations", {}).get(check["request_id"]),
                    task["context"],
                )
                for check in selected["checks"]
            },
            "receipt": receipt,
        }

    def outbox(self, identity, after=0, recent=False):
        if type(after) is not int or not 0 <= after <= 2**63 - 1:
            raise ValueError("Outbox cursor must be a nonnegative 64-bit integer")
        self.expire(identity)
        with self.connect() as db:
            self._row(db, identity)
            direction = "DESC" if recent else "ASC"
            rows = db.execute(
                f"SELECT * FROM outbox WHERE watch_id=? AND id>? ORDER BY id {direction} LIMIT 100",
                (identity, after),
            ).fetchall()
            if recent:
                rows = list(reversed(rows))
        return [
            {
                "id": r["id"],
                "created": r["created"],
                "acked": r["acked"],
                "receipt": self.store.get(json.loads(r["receipt_ref"])),
            }
            for r in rows
        ]

    def ack(self, identity, message_id):
        if type(message_id) is not int or not 1 <= message_id <= 2**63 - 1:
            raise ValueError("Wake-up ID must be a positive 64-bit integer")
        with self.connect() as db:
            changed = db.execute(
                "UPDATE outbox SET acked=COALESCE(acked,?) WHERE id=? AND watch_id=?",
                (self.now(), message_id, identity),
            ).rowcount
            if not changed:
                raise KeyError("Wake-up not found")
