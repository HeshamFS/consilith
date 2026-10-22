"""``peer_messages``: the bodies of agent-to-agent messages, keyed by ``(room, thread, msg_id)``.

The bodies live here and not in the append-only log: the log returns every row to every seated
person and keeps it for good, while these rows can be deleted when a thread closes, after the
retention period, when a participant leaves, and when the room is deleted. The log holds ids only.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from . import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS peer_messages (
  room TEXT NOT NULL, thread TEXT NOT NULL, msg_id TEXT NOT NULL,
  from_person TEXT NOT NULL, to_person TEXT NOT NULL,
  from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
  kind TEXT NOT NULL, hop INTEGER NOT NULL, in_reply_to TEXT,
  body TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}', ts REAL NOT NULL,
  PRIMARY KEY (room, thread, msg_id)
);
CREATE INDEX IF NOT EXISTS peer_messages_ts ON peer_messages (ts);
"""

DAY_S = 86_400.0


@dataclass(frozen=True)
class PeerMessageRow:
    room: str
    thread: str
    msg_id: str
    from_person: str
    to_person: str
    from_agent: str
    to_agent: str
    kind: str
    hop: int
    body: str
    in_reply_to: str | None = None
    meta: dict[str, Any] = field(default_factory=dict[str, Any])
    ts: float = 0


def _message(r: sqlite3.Row) -> PeerMessageRow:
    return PeerMessageRow(
        room=r["room"],
        thread=r["thread"],
        msg_id=r["msg_id"],
        from_person=r["from_person"],
        to_person=r["to_person"],
        from_agent=r["from_agent"],
        to_agent=r["to_agent"],
        kind=r["kind"],
        hop=int(r["hop"]),
        in_reply_to=r["in_reply_to"],
        body=r["body"],
        meta=json.loads(r["meta"]),
        ts=float(r["ts"]),
    )


class PeerMessages:
    def __init__(self, core: Db) -> None:
        self._core = core

    def insert(self, msg: PeerMessageRow, now: float | None = None) -> bool:
        """Store a body. Returns False when ``(room, thread, msg_id)`` is already there, so a
        redelivered message is stored once."""
        ts = (time.time() if now is None else now) if msg.ts == 0 else msg.ts
        with self._core.lock:
            cur = self._core.db.execute(
                "INSERT OR IGNORE INTO peer_messages (room, thread, msg_id, from_person,"
                " to_person, from_agent, to_agent, kind, hop, in_reply_to, body, meta, ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    msg.room,
                    msg.thread,
                    msg.msg_id,
                    msg.from_person,
                    msg.to_person,
                    msg.from_agent,
                    msg.to_agent,
                    msg.kind,
                    msg.hop,
                    msg.in_reply_to,
                    msg.body,
                    json.dumps(msg.meta),
                    ts,
                ),
            )
        return cur.rowcount == 1

    def get(self, room: str, thread: str, msg_id: str) -> PeerMessageRow | None:
        with self._core.lock:
            r = self._core.db.execute(
                "SELECT * FROM peer_messages WHERE room = ? AND thread = ? AND msg_id = ?",
                (room, thread, msg_id),
            ).fetchone()
        return None if r is None else _message(r)

    def list_thread(self, room: str, thread: str) -> list[PeerMessageRow]:
        """The bodies of one thread, oldest first."""
        with self._core.lock:
            rows = self._core.db.execute(
                "SELECT * FROM peer_messages WHERE room = ? AND thread = ? ORDER BY ts, rowid",
                (room, thread),
            ).fetchall()
        return [_message(r) for r in rows]

    def count_thread(self, room: str, thread: str) -> int:
        with self._core.lock:
            (n,) = self._core.db.execute(
                "SELECT COUNT(*) FROM peer_messages WHERE room = ? AND thread = ?", (room, thread)
            ).fetchone()
        return int(n)

    def delete_thread(self, room: str, thread: str) -> int:
        """Remove a thread's bodies (the thread closed)."""
        with self._core.lock:
            cur = self._core.db.execute(
                "DELETE FROM peer_messages WHERE room = ? AND thread = ?", (room, thread)
            )
        return cur.rowcount

    def purge_older_than_days(self, days: float, now: float | None = None) -> int:
        """Delete bodies older than ``days`` days (30 by policy), in every room."""
        ts = time.time() if now is None else now
        with self._core.lock:
            cur = self._core.db.execute(
                "DELETE FROM peer_messages WHERE ts < ?", (ts - days * DAY_S,)
            )
        return cur.rowcount

    def purge_person(self, person: str, room: str | None = None) -> int:
        """Delete every body the person sent or received, in one room or in every room."""
        with self._core.lock:
            if room is None:
                cur = self._core.db.execute(
                    "DELETE FROM peer_messages WHERE from_person = ? OR to_person = ?",
                    (person, person),
                )
            else:
                cur = self._core.db.execute(
                    "DELETE FROM peer_messages WHERE room = ?"
                    " AND (from_person = ? OR to_person = ?)",
                    (room, person, person),
                )
        return cur.rowcount

    def purge_room(self, room: str) -> int:
        with self._core.lock:
            cur = self._core.db.execute("DELETE FROM peer_messages WHERE room = ?", (room,))
        return cur.rowcount
