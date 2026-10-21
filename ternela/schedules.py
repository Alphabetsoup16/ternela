# SPDX-License-Identifier: Apache-2.0
"""Persistence of monitor configuration, claims, fencing and provider deferrals."""

import uuid


class ScheduleRepository:
    def __init__(self, database):
        self.database = database

    def get(self, identity):
        with self.database.connect() as db:
            self.database.watch(db, identity)
            row = db.execute("SELECT * FROM monitors WHERE watch_id=?", (identity,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def configure(self, identity, interval, duration, provider_key):
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO monitor_limits VALUES(?,0)", (provider_key,))
            row = db.execute("SELECT * FROM monitors WHERE watch_id=?", (identity,)).fetchone()
            # Retrying the same start must not steal an active lease or restart its clock.
            if row is None:
                db.execute(
                    "INSERT INTO monitors(watch_id,enabled,interval,due,duration,deadline,provider_key) VALUES(?,1,?,?,?,?,?)",
                    (
                        identity,
                        interval,
                        self.database.clock(),
                        duration,
                        self.database.clock() + duration,
                        provider_key,
                    ),
                )
            elif (
                not row["enabled"]
                or row["interval"] != interval
                or row["duration"] != duration
                or row["deadline"] <= self.database.clock()
            ):
                db.execute(
                    "UPDATE monitors SET enabled=1,interval=?,due=?,lease_token=NULL,"
                    "lease_until=NULL,failures=0,last_error=NULL,duration=?,deadline=? WHERE watch_id=?",
                    (
                        interval,
                        self.database.clock(),
                        duration,
                        self.database.clock() + duration,
                        identity,
                    ),
                )

    def pause(self, identity):
        with self.database.connect() as db:
            self.database.watch(db, identity)
            db.execute(
                "UPDATE monitors SET enabled=0,lease_token=NULL,lease_until=NULL WHERE watch_id=?",
                (identity,),
            )
        return self.get(identity)

    def claim(self, lease_seconds):
        now = self.database.clock()
        with self.database.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE monitors SET enabled=0,lease_token=NULL,lease_until=NULL,last_error='deadline_reached' "
                "WHERE enabled=1 AND deadline<=?",
                (now,),
            )
            row = db.execute(
                "SELECT m.*,w.revision FROM monitors m JOIN watches w ON w.id=m.watch_id "
                "JOIN monitor_limits l ON l.provider=m.provider_key "
                "WHERE l.not_before<=? AND m.enabled=1 AND m.due<=? AND (m.lease_until IS NULL OR m.lease_until<=?) "
                "ORDER BY m.due,m.watch_id LIMIT 1",
                (now, now, now),
            ).fetchone()
            if row is None:
                return None
            claim = dict(row)
            claim["lease_token"] = uuid.uuid4().hex
            db.execute(
                "UPDATE monitors SET lease_token=?,lease_until=?,last_attempt=? WHERE watch_id=?",
                (claim["lease_token"], now + lease_seconds, now, claim["watch_id"]),
            )
        return claim

    def defer_provider(self, key, not_before):
        with self.database.connect() as db:
            db.execute(
                "UPDATE monitor_limits SET not_before=MAX(not_before,?) WHERE provider=?",
                (not_before, key),
            )

    def finish(self, claim, due, failures, error, now):
        with self.database.connect() as db:
            db.execute(
                "UPDATE monitors SET due=?,lease_token=NULL,lease_until=NULL,failures=?,last_success=CASE WHEN ? IS NULL THEN ? ELSE last_success END,last_error=? WHERE watch_id=? AND lease_token=? AND enabled=1",
                (due, failures, error, now, error, claim.watch_id, claim.lease_token),
            )
