"""
Persistence layer built on SQLite (stdlib, no ORM).

Why SQLite
----------
* Zero-dependency — satisfies "server-side persistence" without spinning up
  a separate database process.
* For the required scale (50 concurrent clients, modest write QPS), a
  single-file database is more than enough.

Concurrency
-----------
SQLite allows only one writer at a time per database.  When hit from many
coroutines simultaneously, concurrent opens will deadlock unless we
serialise writes ourselves.  We use one `asyncio.Lock` to gate *all*
DB access, and `asyncio.to_thread` to keep the (blocking) sqlite3 calls
off the event loop.  At our scale the serialisation overhead is invisible
(empirically CPU stays at 0% while idle).

Schema
------
    users          (id, username UNIQUE, password_sha256, created_at)
    groups         (id, group_name UNIQUE, creator_id, created_at)
    group_members  (id, group_id, user_id, joined_at, UNIQUE(group_id,user_id))
    messages       (id, sender_id, target_type, target_name,
                    content_type, content, sent_at)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- setup


class Database:
    """Thin async wrapper around a single SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = asyncio.Lock()

    # ---- connection helpers

    def _connect(self) -> sqlite3.Connection:
        # `timeout` gives us a grace window in case a write is in flight.
        # `isolation_level=None` switches to autocommit, simplifying error
        # handling: each INSERT is its own transaction.
        return sqlite3.connect(self.path, timeout=5.0, isolation_level=None)

    async def init_schema(self) -> None:
        """Create all tables if they do not yet exist.  Safe to call on
        every startup — this is how we preserve persisted data across
        server restarts.

        IMPORTANT: do not drop any table here.  During the interview an
        earlier iteration of this code silently wiped the database on every
        restart, which violated the "persistence across restart" acceptance
        test.  See docs/LESSONS.md #6.
        """

        def _run() -> None:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        username        TEXT UNIQUE NOT NULL,
                        password_sha256 TEXT NOT NULL,
                        created_at      INTEGER DEFAULT (strftime('%s','now'))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS groups (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_name TEXT UNIQUE NOT NULL,
                        creator_id INTEGER,
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        FOREIGN KEY (creator_id) REFERENCES users(id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS group_members (
                        id        INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id  INTEGER NOT NULL,
                        user_id   INTEGER NOT NULL,
                        joined_at INTEGER DEFAULT (strftime('%s','now')),
                        UNIQUE(group_id, user_id),
                        FOREIGN KEY (group_id) REFERENCES groups(id),
                        FOREIGN KEY (user_id)  REFERENCES users(id)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        sender_id    INTEGER NOT NULL,
                        target_type  TEXT NOT NULL,
                        target_name  TEXT NOT NULL,
                        content_type TEXT NOT NULL DEFAULT 'text',
                        content      TEXT NOT NULL,
                        sent_at      INTEGER DEFAULT (strftime('%s','now')),
                        FOREIGN KEY (sender_id) REFERENCES users(id)
                    )
                    """
                )
                # Useful indexes for the two most common query patterns.
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_target "
                    "ON messages(target_type, target_name, sent_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_group_members_user "
                    "ON group_members(user_id)"
                )
            finally:
                conn.close()

        async with self._lock:
            await asyncio.to_thread(_run)
        logger.info("database ready at %s", self.path)

    # ---- generic executors

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run an INSERT/UPDATE/DELETE. Returns the lastrowid (0 if n/a)."""

        def _run() -> int:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(sql, tuple(params))
                return cur.lastrowid or 0
            finally:
                conn.close()

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def fetchone(
        self, sql: str, params: Iterable[Any] = ()
    ) -> Optional[tuple]:
        def _run() -> Optional[tuple]:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(sql, tuple(params))
                return cur.fetchone()
            finally:
                conn.close()

        async with self._lock:
            return await asyncio.to_thread(_run)

    async def fetchall(
        self, sql: str, params: Iterable[Any] = ()
    ) -> list[tuple]:
        def _run() -> list[tuple]:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(sql, tuple(params))
                return cur.fetchall()
            finally:
                conn.close()

        async with self._lock:
            return await asyncio.to_thread(_run)


# --------------------------------------------------------------------- utils


def sha256_hex(s: str) -> str:
    """Hash a password for storage.  Not suitable for production — a real
    system would use a KDF such as argon2/scrypt/bcrypt with per-user salt.
    The interview spec calls for plaintext-OK, so SHA-256 is already above
    the bar; the upgrade path is a one-line change here."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
