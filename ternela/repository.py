# SPDX-License-Identifier: Apache-2.0
"""Watch persistence, ordered delivery and atomic receipt/outbox accounting."""

import json
import re
from copy import deepcopy

from .contracts import (
    FrozenJSON,
    PreparedObservation,
    WatchSnapshot,
    read_record,
    validate_record_ref,
    write_record,
)
from .errors import DeliveryNotFound, EventConflict, ObservationConflict
from .observations import digest, expired_receipt, receipt_summary


class WatchRepository:
    def __init__(self, database):
        self.database = database
        self.store = database.store

    def connect(self):
        return self.database.connect()

    def now(self):
        return self.database.clock()

    def _row(self, db, identity):
        return self.database.watch(db, identity)

    def insert(self, identity, task, plan, phase, ttl):
        task_ref = self.store.put(task)
        plan_ref = write_record(self.store, plan, "plan")
        now = self.now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO watches(id,task_ref,plan_ref,phase,ttl,created,updated) VALUES(?,?,?,?,?,?,?)",
                (identity, json.dumps(task_ref), json.dumps(plan_ref), phase, ttl, now, now),
            )

    def load(self, identity):
        with self.connect() as db:
            row = dict(self._row(db, identity))
        task = self.store.get(json.loads(row["task_ref"]))
        plan = read_record(self.store, json.loads(row["plan_ref"]), "plan")
        receipt = (
            read_record(self.store, json.loads(row["receipt_ref"]), "receipt")
            if row["receipt_ref"]
            else None
        )
        return row, task, plan, receipt

    def snapshot(self, identity):
        with self.connect() as db:
            row = self._row(db, identity)
        task_ref, plan_ref = json.loads(row["task_ref"]), json.loads(row["plan_ref"])
        return WatchSnapshot(
            identity,
            row["revision"],
            row["phase"],
            row["ttl"],
            task_ref,
            plan_ref,
            FrozenJSON.capture(self.store.get(task_ref)),
            FrozenJSON.capture(read_record(self.store, plan_ref, "plan")),
        )

    def _commit(self, db, row, receipt, *, expiry=False, fence=None):
        ref = write_record(self.store, receipt, "receipt")
        # A slow blob write must not publish a fresh success after its TTL.
        now = self.now()
        if not receipt["result"].get("stale") and now >= receipt["expires_at"]:
            receipt = expired_receipt(receipt, now)
            ref = write_record(self.store, receipt, "receipt")
        self.database.require_lease(db, fence, self.now())
        decision = digest(receipt["result"])
        db.execute(
            "UPDATE watches SET receipt_ref=?,decision=?,updated=?,expires=?,stale=?,decision_status=? WHERE id=?",
            (
                json.dumps(ref),
                decision,
                self.now(),
                receipt["expires_at"],
                int(bool(receipt["result"].get("stale"))),
                receipt["result"]["status"],
                row["id"],
            ),
        )
        db.execute("INSERT OR IGNORE INTO watch_usage(watch_id) VALUES(?)", (row["id"],))
        # Expiry reuses the previous input/trace: it must never count as another read.
        db.execute(
            "UPDATE watch_usage SET observations=observations+?,read_attempts=read_attempts+?,"
            "unknown_observations=unknown_observations+?,decision_changes=decision_changes+?,"
            "expiry_events=expiry_events+? WHERE watch_id=?",
            (
                int(not expiry),
                0 if expiry else receipt_summary(receipt)["read_attempts"],
                int(not expiry and receipt["result"]["status"] == "unknown"),
                int(decision != row["decision"]),
                int(expiry),
                row["id"],
            ),
        )
        if decision != row["decision"]:
            db.execute(
                "INSERT INTO outbox(watch_id,receipt_ref,created) VALUES(?,?,?)",
                (row["id"], json.dumps(ref), self.now()),
            )
        return ref

    def commit(self, prepared: PreparedObservation, fence=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self.now()
            row = self._row(db, prepared.watch_id)
            self.database.require_lease(db, fence, now)
            validate_record_ref(json.loads(row["plan_ref"]), "plan")
            if prepared.observed_at > now + 1:
                raise ObservationConflict("Observation time is now in the future")
            if (
                json.loads(row["plan_ref"])["sha256"] != prepared.plan_sha256
                or json.loads(row["task_ref"])["sha256"] != prepared.task_sha256
                or row["phase"] != prepared.phase
            ):
                raise ObservationConflict(
                    "Prepared observation no longer matches the watch plan and context"
                )
            if row["expires"] is not None and not row["stale"] and now >= row["expires"]:
                prior = read_record(self.store, json.loads(row["receipt_ref"]), "receipt")
                self._commit(db, row, expired_receipt(prior, now), expiry=True)
                row = self._row(db, prepared.watch_id)
            old = db.execute(
                "SELECT * FROM events WHERE watch_id=? AND event_id=?",
                (prepared.watch_id, prepared.event_id),
            ).fetchone()
            if old:
                if old["fingerprint"] != prepared.fingerprint:
                    raise EventConflict("Event ID reused with different content")
                return {
                    "duplicate": True,
                    "accepted": bool(old["accepted"]),
                    "receipt": read_record(self.store, json.loads(row["receipt_ref"]), "receipt")
                    if row["receipt_ref"]
                    else None,
                    "delivery_receipt": read_record(
                        self.store, json.loads(old["receipt_ref"]), "receipt"
                    )
                    if old["receipt_ref"]
                    else None,
                }
            prior = (
                read_record(self.store, json.loads(row["receipt_ref"]), "receipt")
                if row["receipt_ref"]
                else None
            )
            accepted = prepared.revision > row["revision"] and (
                prior is None or prepared.observed_at >= prior["observed_at"]
            )
            ref = None
            if accepted:
                receipt = prepared.receipt.value()
                receipt.update(evaluated_at=now, expires_at=prepared.observed_at + row["ttl"])
                if now >= receipt["expires_at"]:
                    receipt = expired_receipt(receipt, now)
                ref = self._commit(db, row, receipt, fence=fence)
                db.execute(
                    "UPDATE watches SET revision=? WHERE id=?",
                    (prepared.revision, prepared.watch_id),
                )
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?)",
                (
                    prepared.watch_id,
                    prepared.event_id,
                    prepared.fingerprint,
                    prepared.revision,
                    int(accepted),
                    json.dumps(ref) if ref else None,
                ),
            )
        return {
            "duplicate": False,
            "accepted": accepted,
            "receipt": read_record(self.store, ref, "receipt") if ref else None,
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
                receipt = read_record(self.store, json.loads(row["receipt_ref"]), "receipt")
                if not receipt["result"].get("stale") and self.now() >= receipt["expires_at"]:
                    receipt = deepcopy(receipt)
                    receipt["evaluated_at"] = self.now()
                    receipt["result"] = {
                        "status": "unknown",
                        "checks": [],
                        "gaps": [{"limitation": "Evidence expired; obtain a new observation"}],
                        "stale": True,
                    }
                    self._commit(db, row, receipt, expiry=True)

    def directory(self, after="", limit=30):
        """Stable ID keyset pagination; newly inserted lower IDs appear on refresh."""
        if not isinstance(after, str) or (after and not re.fullmatch(r"[a-f0-9]{32}", after)):
            raise ValueError("Watch cursor must be an exact watch ID")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Directory limit must be 1–100")
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM watches WHERE id>? ORDER BY id LIMIT ?", (after, limit + 1)
            ).fetchall()
        items = []
        now = self.now()
        for row in rows[:limit]:
            validate_record_ref(json.loads(row["plan_ref"]), "plan")
            if row["receipt_ref"]:
                validate_record_ref(json.loads(row["receipt_ref"]), "receipt")
            task = self.store.get(json.loads(row["task_ref"]))
            stale = row["expires"] is not None and row["expires"] <= now
            items.append(
                {
                    "id": row["id"],
                    "provider": task["provider"],
                    "task_id": task["id"],
                    "context": task["context"],
                    "name": task["task"][:160],
                    "phase": row["phase"],
                    "created": row["created"],
                    "revision": row["revision"],
                    "status": "unknown" if stale else row["decision_status"] or "unchecked",
                }
            )
        return {"items": items, "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    def usage(self, identity=None):
        fields = (
            "observations",
            "read_attempts",
            "unknown_observations",
            "decision_changes",
            "expiry_events",
            "network_reads",
            "shared_reads",
        )
        with self.connect() as db:
            if identity is not None:
                self._row(db, identity)
            query = (
                "SELECT "
                + ",".join("COALESCE(SUM(" + f + "),0) AS " + f for f in fields)
                + " FROM watch_usage"
            )
            return dict(
                db.execute(
                    query + (" WHERE watch_id=?" if identity else ""),
                    (identity,) if identity else (),
                ).fetchone()
            )

    def record_acquisition(self, identity, network_reads, shared_reads):
        """Account completed local acquisition attempts, including fenced/failed reads."""
        if any(type(n) is not int or n < 0 for n in (network_reads, shared_reads)):
            raise ValueError("Read counters must be nonnegative integers")
        with self.connect() as db:
            self._row(db, identity)
            db.execute("INSERT OR IGNORE INTO watch_usage(watch_id) VALUES(?)", (identity,))
            db.execute(
                "UPDATE watch_usage SET network_reads=network_reads+?,shared_reads=shared_reads+? WHERE watch_id=?",
                (network_reads, shared_reads, identity),
            )

    @staticmethod
    def consumer_name(name):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
            raise ValueError(
                "Consumer must be 1–64 ASCII letters, digits, dots, underscores or hyphens"
            )
        return name

    def deliveries(self, identity, consumer):
        """At-least-once ordered delivery; only an explicit consumer ack advances it."""
        self.consumer_name(consumer)
        self.expire(identity)
        with self.connect() as db:
            self._row(db, identity)
            row = db.execute(
                "SELECT cursor FROM consumers WHERE watch_id=? AND name=?", (identity, consumer)
            ).fetchone()
        return self.outbox(identity, row["cursor"] if row else 0, compact=True)

    def acknowledge_delivery(self, identity, consumer, message_id):
        self.consumer_name(consumer)
        if type(message_id) is not int or not 1 <= message_id <= 2**63 - 1:
            raise ValueError("Wake-up ID must be a positive 64-bit integer")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._row(db, identity)
            message = db.execute(
                "SELECT id FROM outbox WHERE watch_id=? AND id=?", (identity, message_id)
            ).fetchone()
            if message is None:
                raise DeliveryNotFound("Wake-up not found")
            db.execute("INSERT OR IGNORE INTO consumers VALUES(?,?,0)", (identity, consumer))
            cursor = db.execute(
                "SELECT cursor FROM consumers WHERE watch_id=? AND name=?", (identity, consumer)
            ).fetchone()[0]
            if message_id > cursor:
                head = db.execute(
                    "SELECT MIN(id) FROM outbox WHERE watch_id=? AND id>?", (identity, cursor)
                ).fetchone()[0]
                if message_id != head:
                    raise ValueError("Acknowledge the earliest pending delivery first")
                db.execute(
                    "UPDATE consumers SET cursor=? WHERE watch_id=? AND name=?",
                    (message_id, identity, consumer),
                )
        return {"acknowledged": message_id, "consumer": consumer}

    def outbox(self, identity, after=0, recent=False, compact=False):
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
                "receipt": (
                    receipt_summary(
                        read_record(self.store, json.loads(r["receipt_ref"]), "receipt")
                    )
                    if compact
                    else read_record(self.store, json.loads(r["receipt_ref"]), "receipt")
                ),
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
                raise DeliveryNotFound("Wake-up not found")
