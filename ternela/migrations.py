# SPDX-License-Identifier: Apache-2.0
"""Ordered SQLite upgrades from legacy databases; receipts and source blobs remain immutable."""

import json
from dataclasses import dataclass
from typing import Callable

from .contracts import read_record
from .errors import UnsupportedVersion


def columns(db, table, declarations):
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, declaration in declarations:
        if name not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def watches(db, store):
    for sql in (
        "CREATE TABLE IF NOT EXISTS watches(id TEXT PRIMARY KEY,task_ref TEXT,plan_ref TEXT,phase TEXT,ttl REAL,created REAL,revision INTEGER DEFAULT 0,receipt_ref TEXT,decision TEXT,updated REAL,expires REAL,stale INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS events(watch_id TEXT,event_id TEXT,fingerprint TEXT,revision INTEGER,accepted INTEGER,receipt_ref TEXT,PRIMARY KEY(watch_id,event_id))",
        "CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,watch_id TEXT,receipt_ref TEXT,created REAL,acked REAL)",
    ):
        db.execute(sql)
    columns(db, "watches", (("expires", "REAL"), ("stale", "INTEGER DEFAULT 0")))
    for row in db.execute(
        "SELECT * FROM watches WHERE receipt_ref IS NOT NULL AND expires IS NULL"
    ).fetchall():
        receipt = read_record(store, json.loads(row["receipt_ref"]), "receipt")
        db.execute(
            "UPDATE watches SET expires=?,stale=? WHERE id=?",
            (receipt["expires_at"], int(bool(receipt["result"].get("stale"))), row["id"]),
        )
    db.execute("CREATE INDEX IF NOT EXISTS watches_expiry ON watches(stale,expires)")
    db.execute("CREATE INDEX IF NOT EXISTS outbox_watch_cursor ON outbox(watch_id,id)")


def schedules(db, store):
    db.execute(
        "CREATE TABLE IF NOT EXISTS monitors(watch_id TEXT PRIMARY KEY REFERENCES watches(id),enabled INTEGER NOT NULL,interval REAL NOT NULL,due REAL NOT NULL,lease_until REAL,lease_token TEXT,failures INTEGER NOT NULL DEFAULT 0,last_attempt REAL,last_success REAL,last_error TEXT,duration REAL NOT NULL DEFAULT 86400,deadline REAL)"
    )
    columns(
        db,
        "monitors",
        (
            ("duration", "REAL NOT NULL DEFAULT 86400"),
            ("deadline", "REAL"),
            ("provider_key", "TEXT NOT NULL DEFAULT 'github-public'"),
        ),
    )
    db.execute("UPDATE monitors SET deadline=due+duration WHERE deadline IS NULL")
    db.execute("CREATE INDEX IF NOT EXISTS monitors_due ON monitors(enabled,due)")
    db.execute("CREATE INDEX IF NOT EXISTS monitors_deadline ON monitors(enabled,deadline)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS monitor_limits(provider TEXT PRIMARY KEY,not_before REAL NOT NULL)"
    )
    db.execute("INSERT OR IGNORE INTO monitor_limits VALUES('github-public',0)")


def delivery_and_usage(db, store):
    db.execute(
        "CREATE TABLE IF NOT EXISTS consumers(watch_id TEXT,name TEXT,cursor INTEGER NOT NULL,PRIMARY KEY(watch_id,name))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS watch_usage(watch_id TEXT PRIMARY KEY,observations INTEGER NOT NULL DEFAULT 0,read_attempts INTEGER NOT NULL DEFAULT 0,unknown_observations INTEGER NOT NULL DEFAULT 0,decision_changes INTEGER NOT NULL DEFAULT 0,expiry_events INTEGER NOT NULL DEFAULT 0,network_reads INTEGER NOT NULL DEFAULT 0,shared_reads INTEGER NOT NULL DEFAULT 0)"
    )
    columns(db, "watches", (("decision_status", "TEXT"),))
    for row in db.execute(
        "SELECT id,receipt_ref FROM watches WHERE receipt_ref IS NOT NULL AND decision_status IS NULL"
    ).fetchall():
        receipt = read_record(store, json.loads(row["receipt_ref"]), "receipt")
        db.execute(
            "UPDATE watches SET decision_status=? WHERE id=?",
            (receipt["result"]["status"], row["id"]),
        )


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable


MIGRATIONS = (
    Migration(1, "watch receipts and outbox", watches),
    Migration(2, "fenced public schedules", schedules),
    Migration(3, "consumer checkpoints and usage", delivery_and_usage),
)
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(db, store, now, migrations=MIGRATIONS):
    if [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
        raise ValueError("Migrations must have consecutive versions starting at one")
    # Reject newer files before changing journal mode or creating any tables.
    current = db.execute("PRAGMA user_version").fetchone()[0]
    if current > len(migrations):
        raise UnsupportedVersion(f"Unsupported database schema version {current}")
    db.execute("BEGIN IMMEDIATE")
    current = db.execute("PRAGMA user_version").fetchone()[0]
    if current > len(migrations):
        raise UnsupportedVersion(f"Unsupported database schema version {current}")
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at REAL NOT NULL)"
    )
    history = db.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
    if [(row[0], row[1]) for row in history] != [(m.version, m.name) for m in migrations[:current]]:
        raise UnsupportedVersion("Database migration history does not match its schema version")
    for migration in migrations[current:]:
        migration.apply(db, store)
        db.execute(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            (migration.version, migration.name, now()),
        )
        db.execute(f"PRAGMA user_version={migration.version}")
