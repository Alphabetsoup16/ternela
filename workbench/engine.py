# SPDX-License-Identifier: Apache-2.0
"""Persistent, event-fed watches with immutable receipts and a transactional outbox.

This is a single-host prototype. Events are complete observation snapshots supplied
by an authenticated caller, not verified webhooks or independently ordered facts.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from docs_lab.audit import audit_check, audit_request
from docs_lab.v2.runtime import execute, evaluate
from expansion_lab.runtime import enforce_guard
from expansion_lab.schema import PLAN
from expansion_lab.storage import Store
from expansion_lab.storage import PROJECT, sha
from observation_lab.schema import validate_shape
from safety_lab.runtime import preservation_issues
from source_lab.evidence import SourceIndex


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def admit(plan, task, indexes=None):
    validate_shape(plan, PLAN)
    if plan["task_id"] != task["id"]: raise ValueError("Plan/task identity mismatch")
    index = (indexes or {}).get(task["provider"]) or SourceIndex(task["provider"])
    requests = {r["id"]: r for r in plan["requests"]}
    issues = preservation_issues(plan)
    for requirement in plan["requirements"]:
        if requirement["coverage"] == "observable" and not requirement["check_ids"]:
            issues.append("Observable requirements must name executable checks")
    for request in plan["requests"]:
        # Only this non-mutating POST has a reviewed observation purpose.
        if not request["operation"].startswith("GET ") and request["operation"] != "POST /v1/invoices/create_preview":
            issues.append("Operation has no reviewed read-only execution policy")
        issues += audit_request(index, request, task, {})["issues"]
        for key in ["bindings_json"]: json.loads(request[key])
    for check in plan["checks"]:
        issues += audit_check(index, check, requests.get(check["request_id"]))["issues"]
        json.loads(check["selector_json"]); json.loads(check["value_json"])
    if issues: raise ValueError("Plan admission failed: " + "; ".join(dict.fromkeys(issues)))
    pins=json.loads((PROJECT/"data/sources/manifest.json").read_text())
    return {"pinned_source":next(p for p in pins if p["provider"]==task["provider"]),
            "supplements":getattr(index,"supplements",[]),"validator_sha256":sha(Path(__file__).read_bytes())}


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
            if guard["target_request_id"] in needed: needed.add(guard["source_request_id"])
        for identity in list(needed):
            if identity not in by_id: raise ValueError("Missing request dependency")
            needed.update(re.findall(r"\$response\.([^/\"\s]+)(?:/|\")", by_id[identity]["bindings_json"]))
        if needed == old: break
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
                    try: enforce_guard(guard, request, context, observed)
                    except (ValueError, KeyError, TypeError, IndexError) as exc:
                        failures.append({"id": guard["id"], "reason": str(exc)}); raise
            pages = snapshots.get(request["id"], [])
            if isinstance(pages, dict): pages = [pages]
            cursor = iter(pages)
            def transport(call):
                return deepcopy(next(cursor, {"http_status": 0, "body": None}))
            observed[request["id"]] = execute(request, context, transport, observed)
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            observed[request["id"]] = {"http_status": 0, "body": None, "collection_complete": False, "traversal_reason": str(exc), "trace": []}
    result = evaluate(selected, observed, context, phase)
    if failures: result.update(status="unknown", preservation_failures=failures)
    return result, observed


class Watches:
    def __init__(self, path, store=None, clock=time.time):
        self.path = str(path); self.store = store or Store(); self.clock = clock
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS watches(id TEXT PRIMARY KEY, task_ref TEXT, plan_ref TEXT,
                phase TEXT, ttl REAL, created REAL, revision INTEGER DEFAULT 0,
                receipt_ref TEXT, decision TEXT, updated REAL, expires REAL, stale INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS events(watch_id TEXT, event_id TEXT, fingerprint TEXT,
                revision INTEGER, accepted INTEGER, receipt_ref TEXT, PRIMARY KEY(watch_id,event_id));
              CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, watch_id TEXT,
                receipt_ref TEXT, created REAL, acked REAL);
            ''')
            columns={r[1] for r in db.execute("PRAGMA table_info(watches)")}
            for name, declaration in [("expires","REAL"),("stale","INTEGER DEFAULT 0")]:
                if name not in columns: db.execute(f"ALTER TABLE watches ADD COLUMN {name} {declaration}")
            for row in db.execute("SELECT * FROM watches WHERE receipt_ref IS NOT NULL AND expires IS NULL").fetchall():
                receipt=self.store.get(json.loads(row["receipt_ref"]))
                db.execute("UPDATE watches SET expires=?,stale=? WHERE id=?",(receipt["expires_at"],int(bool(receipt["result"].get("stale"))),row["id"]))
            db.execute("CREATE INDEX IF NOT EXISTS watches_expiry ON watches(stale,expires)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15); db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db: yield db
        finally: db.close()

    def create(self, task, plan, phase="after", ttl=30, indexes=None):
        if phase not in ["before", "after"]: raise ValueError("Invalid phase")
        if type(ttl) not in [int, float] or not math.isfinite(ttl) or not 1 <= ttl <= 86400: raise ValueError("TTL must be 1–86400 seconds")
        admission=admit(plan, task, indexes)
        task=dict(task,_admission=admission)
        phase_plan(plan, phase)
        identity = uuid.uuid4().hex; now = self.clock()
        task_ref, plan_ref = self.store.put(task), self.store.put(plan)
        with self.connect() as db:
            db.execute("INSERT INTO watches(id,task_ref,plan_ref,phase,ttl,created,updated) VALUES(?,?,?,?,?,?,?)",
                       (identity,json.dumps(task_ref),json.dumps(plan_ref),phase,ttl,now,now))
        return self.get(identity)

    def _row(self, db, identity):
        row = db.execute("SELECT * FROM watches WHERE id=?", (identity,)).fetchone()
        if row is None: raise KeyError("Watch not found")
        return row

    def _commit(self, db, row, receipt):
        ref = self.store.put(receipt)
        decision = digest(receipt["result"])
        db.execute("UPDATE watches SET receipt_ref=?,decision=?,updated=?,expires=?,stale=? WHERE id=?", (json.dumps(ref),decision,self.clock(),receipt["expires_at"],int(bool(receipt["result"].get("stale"))),row["id"]))
        if decision != row["decision"]:
            db.execute("INSERT INTO outbox(watch_id,receipt_ref,created) VALUES(?,?,?)", (row["id"],json.dumps(ref),self.clock()))
        return ref

    def submit(self, identity, event_id, revision, snapshots, observed_at=None, mode="caller-supplied snapshot"):
        # A duplicate may arrive after expiry or after a newer event. Never return
        # its historical success as the current decision.
        self.expire()
        supplied_time = observed_at
        now = self.clock(); observed_at = now if observed_at is None else observed_at
        if not isinstance(event_id,str) or not 1 <= len(event_id) <= 160: raise ValueError("Invalid event ID")
        if type(revision) is not int or revision < 1: raise ValueError("Revision must be a positive integer")
        if type(observed_at) not in [int,float] or not math.isfinite(observed_at) or observed_at < 0 or observed_at > now + 1: raise ValueError("Invalid observation time")
        if not isinstance(snapshots,dict): raise ValueError("Snapshots must map request IDs to captured responses")
        fingerprint = digest({"revision":revision,"snapshots":snapshots,"observed_at":supplied_time,"mode":mode})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, identity)
            old = db.execute("SELECT * FROM events WHERE watch_id=? AND event_id=?",(identity,event_id)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint: raise ValueError("Event ID reused with different content")
                return {"duplicate":True,"accepted":bool(old["accepted"]),
                        "receipt":self.store.get(json.loads(row["receipt_ref"])) if row["receipt_ref"] else None,
                        "delivery_receipt":self.store.get(json.loads(old["receipt_ref"])) if old["receipt_ref"] else None}
            # Caller revision and source time must both move forward; this is one ordered source lane.
            prior = self.store.get(json.loads(row["receipt_ref"])) if row["receipt_ref"] else None
            accepted = revision > row["revision"] and (prior is None or observed_at >= prior["observed_at"])
            ref = None
            if accepted:
                task, plan = self.store.get(json.loads(row["task_ref"])), self.store.get(json.loads(row["plan_ref"]))
                result, observations = observe(plan,task["context"],snapshots,row["phase"])
                receipt = {"watch_id":identity,"event_id":event_id,"revision":revision,"observed_at":observed_at,"evaluated_at":now,
                           "expires_at":observed_at+row["ttl"],"phase":row["phase"],"result":result,"observations":observations,"input":snapshots,
                           "plan_sha256":json.loads(row["plan_ref"])["sha256"],"source_evidence":task["_admission"],"mode":mode}
                if now >= receipt["expires_at"]: receipt["result"] = {"status":"unknown","checks":[],"gaps":[{"limitation":"Evidence expired; obtain a new observation"}],"stale":True}
                ref = self._commit(db,row,receipt)
                db.execute("UPDATE watches SET revision=? WHERE id=?",(revision,identity))
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?)",(identity,event_id,fingerprint,revision,int(accepted),json.dumps(ref) if ref else None))
        return {"duplicate":False,"accepted":accepted,"receipt":self.store.get(ref) if ref else None}

    def expire(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM watches WHERE stale=0 AND expires<=?",(self.clock(),)).fetchall():
                receipt = self.store.get(json.loads(row["receipt_ref"]))
                if not receipt["result"].get("stale") and self.clock() >= receipt["expires_at"]:
                    receipt = deepcopy(receipt); receipt["evaluated_at"] = self.clock()
                    receipt["result"] = {"status":"unknown","checks":[],"gaps":[{"limitation":"Evidence expired; obtain a new observation"}],"stale":True}
                    self._commit(db,row,receipt)

    def get(self, identity):
        self.expire()
        with self.connect() as db: row = self._row(db,identity)
        return {"id":identity,"phase":row["phase"],"ttl":row["ttl"],"revision":row["revision"],
                "task":self.store.get(json.loads(row["task_ref"])),"plan":self.store.get(json.loads(row["plan_ref"])),
                "receipt":self.store.get(json.loads(row["receipt_ref"])) if row["receipt_ref"] else None}

    def outbox(self, identity, after=0, recent=False):
        self.get(identity)
        with self.connect() as db:
            direction="DESC" if recent else "ASC"
            rows = db.execute(f"SELECT * FROM outbox WHERE watch_id=? AND id>? ORDER BY id {direction} LIMIT 100",(identity,after)).fetchall()
            if recent: rows=list(reversed(rows))
        return [{"id":r["id"],"created":r["created"],"acked":r["acked"],"receipt":self.store.get(json.loads(r["receipt_ref"]))} for r in rows]

    def ack(self, identity, message_id):
        with self.connect() as db:
            changed = db.execute("UPDATE outbox SET acked=COALESCE(acked,?) WHERE id=? AND watch_id=?",(self.clock(),message_id,identity)).rowcount
            if not changed: raise KeyError("Wake-up not found")
