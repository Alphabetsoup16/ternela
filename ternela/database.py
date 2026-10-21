# SPDX-License-Identifier: Apache-2.0
"""One database boundary shared by watch and schedule repositories."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .errors import LeaseExpired, WatchNotFound
from .migrations import migrate


class Database:
    def __init__(self, path, store, clock):
        self.path, self.store, self.clock = str(path), store, clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            migrate(db, store, clock)
        self.enable_wal()

    def enable_wal(self):
        # Changing journal mode can return BUSY immediately despite busy_timeout.
        # Close each competing connection before retrying; retain a bounded wait.
        deadline = time.monotonic() + 15
        while True:
            try:
                with self.connect() as db:
                    if db.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
                        return
                    if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                        raise RuntimeError("SQLite did not enable WAL mode")
                    return
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
                if (
                    code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                    or time.monotonic() >= deadline
                ):
                    raise
                time.sleep(0.01)

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

    @staticmethod
    def watch(db, identity):
        row = db.execute("SELECT * FROM watches WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise WatchNotFound("Watch not found")
        return row

    @staticmethod
    def require_lease(db, fence, now):
        if fence is None:
            return
        row = db.execute(
            "SELECT enabled,lease_token,lease_until,deadline FROM monitors WHERE watch_id=?",
            (fence.watch_id,),
        ).fetchone()
        if (
            row is None
            or not row["enabled"]
            or row["lease_token"] != fence.token
            or row["lease_until"] is None
            or row["lease_until"] <= now
            or row["deadline"] <= now
        ):
            raise LeaseExpired("Monitor lease is no longer active")
