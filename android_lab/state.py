from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

from .common import LabError

ACTIVE = ("active", "quarantined")


class State:
    def __init__(self, home):
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        self.db = home / "state.sqlite"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                    status TEXT NOT NULL, expires REAL NOT NULL,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leases (
                    resource TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS invalidated (alias TEXT PRIMARY KEY, failures_since REAL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(id),
                    at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
                );
            """)
            db.execute("BEGIN IMMEDIATE")
            if "failures_since" not in {row["name"] for row in db.execute("PRAGMA table_info(invalidated)")}:
                db.execute("ALTER TABLE invalidated ADD COLUMN failures_since REAL")
                db.execute("UPDATE invalidated SET failures_since = ?", (time.time(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()

    def acquire(self, resources, duration, data):
        session = secrets.token_hex(16)
        token = secrets.token_urlsafe(32)
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO sessions VALUES (?, ?, 'active', ?, 0, ?)",
                           (session, self.token_hash(token), time.time() + duration, json.dumps(data)))
                db.executemany("INSERT INTO leases VALUES (?, ?)", [(r, session) for r in set(resources)])
        except sqlite3.IntegrityError as err:
            raise LabError("resource_busy", "A requested resource is leased or awaiting recovery") from err
        return session, token

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def get(self, session, token=None, *, active=False):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE id = ?", (session,)).fetchone()
        if row is None or (token is not None and not secrets.compare_digest(row["token_hash"], self.token_hash(token))):
            raise LabError("invalid_session", "Unknown session or invalid session token")
        if active and (row["status"] != "active" or row["expires"] <= time.time() or row["cancelled"]):
            raise LabError("stale_session", "Session is expired, cancelled, or closed")
        return {k: row[k] for k in ("id", "status", "expires", "cancelled")} | {"data": json.loads(row["data"])}

    def update(self, session, data):
        with self.connect() as db:
            db.execute("UPDATE sessions SET data = ? WHERE id = ?", (json.dumps(data), session))

    def event(self, session, kind, **data):
        with self.connect() as db:
            db.execute("INSERT INTO events (session, at, kind, data) VALUES (?, ?, ?, ?)",
                       (session, time.time(), kind, json.dumps(data)))

    def events(self, session):
        with self.connect() as db:
            return [{"time": r["at"], "event": r["kind"], **json.loads(r["data"])}
                    for r in db.execute("SELECT * FROM events WHERE session = ? ORDER BY seq", (session,))]

    def all(self, status=None):
        with self.connect() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, status, expires, cancelled FROM sessions WHERE ? IS NULL OR status = ? ORDER BY rowid DESC",
                (status, status))]

    def leased(self):
        with self.connect() as db:
            return {r["resource"] for r in db.execute("SELECT resource FROM leases")}

    def release(self, session, cleaned):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if cleaned:
                db.execute("DELETE FROM leases WHERE session = ?", (session,))
            db.execute("UPDATE sessions SET status = ? WHERE id = ?", ("closed" if cleaned else "quarantined", session))

    def release_resource(self, session, resource):
        with self.connect() as db:
            db.execute("DELETE FROM leases WHERE session = ? AND resource = ?", (session, resource))

    def stop(self, session):
        with self.connect() as db:
            db.execute("UPDATE sessions SET cancelled = 1 WHERE id = ? AND status != 'closed'", (session,))

    def cancelled(self, session):
        row = self.get(session)
        return row["cancelled"] or row["expires"] <= time.time() or row["status"] != "active"

    @contextmanager
    def operation(self, session):
        self.get(session)
        lock_dir = self.home / "locks"
        lock_dir.mkdir(exist_ok=True)
        with (lock_dir / (session + ".lock")).open("a+b") as file:
            if file.tell() == 0:
                file.write(b"\0")
                file.flush()
            file.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as err:
                raise LabError("operation_busy", "Wait for the current operation to end before recovery") from err
            yield
    def invalidate(self, alias):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO invalidated VALUES (?, ?)", (alias, time.time()))

    def revoke(self, alias):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO invalidated VALUES (?, ?)", (alias, time.time()))
            db.execute(
                "UPDATE sessions SET cancelled = 1 WHERE status = 'active' AND json_extract(data, '$.planned_device') = ?",
                (alias,),
            )

    def accept_qualification(self, alias, observed, durable=None):
        invalidated = self.invalidated()
        if observed <= max(invalidated.get(alias, 0), invalidated.get(durable, 0)):
            raise LabError("stale_qualification", "Device requires qualification observed after the latest invalidation")

    def invalidated(self):
        with self.connect() as db:
            return {row["alias"]: row["failures_since"] for row in db.execute("SELECT * FROM invalidated")}
