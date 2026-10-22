"""``peer_deliveries``: the durable queue of peer messages waiting for the recipient's agent.

Delivery is asynchronous (a model call can outlast the A2A timeout) and must survive a restart,
which the A2A task store does not. A worker claims the next queued delivery with a lease. A
delivery whose lease runs out, for example because the process died, is requeued.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from . import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS peer_deliveries (
  id TEXT NOT NULL, room TEXT NOT NULL, thread TEXT NOT NULL,
  from_person TEXT NOT NULL, to_person TEXT NOT NULL, to_agent TEXT NOT NULL,
  payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  lease_until REAL, reason TEXT, created REAL NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY (room, id)
);
CREATE INDEX IF NOT EXISTS peer_deliveries_queue ON peer_deliveries (status, created);
CREATE INDEX IF NOT EXISTS peer_deliveries_lease ON peer_deliveries (status, lease_until);
"""

DeliveryStatus = Literal["queued", "leased", "done", "failed", "undeliverable"]


@dataclass(frozen=True)
class PeerDelivery:
    """One queued delivery. ``id`` is the envelope id, the idempotency key within the room."""

    id: str
    room: str
    thread: str
    from_person: str
    to_person: str
    to_agent: str
    payload: dict[str, Any] = field(default_factory=dict[str, Any])  # ids only, never the body
    status: DeliveryStatus = "queued"
    attempts: int = 0
    lease_until: float | None = None
    reason: str | None = None
    created: float = 0
    updated: float = 0


def _delivery(r: sqlite3.Row) -> PeerDelivery:
    return PeerDelivery(
        id=r["id"],
        room=r["room"],
        thread=r["thread"],
        from_person=r["from_person"],
        to_person=r["to_person"],
        to_agent=r["to_agent"],
        payload=json.loads(r["payload"]),
        status=r["status"],
        attempts=int(r["attempts"]),
        lease_until=None if r["lease_until"] is None else float(r["lease_until"]),
        reason=r["reason"],
        created=float(r["created"]),
        updated=float(r["updated"]),
    )


class PeerDeliveries:
    def __init__(self, core: Db) -> None:
        self._core = core

    def enqueue(
        self,
        room: str,
        delivery_id: str,
        thread: str,
        from_person: str,
        to_person: str,
        to_agent: str,
        payload: dict[str, Any],
        now: float | None = None,
    ) -> bool:
        """Queue a delivery. Returns False when ``(room, id)`` is already queued, so delivering
        one envelope id twice is one delivery."""
        ts = time.time() if now is None else now
        with self._core.lock:
            cur = self._core.db.execute(
                "INSERT OR IGNORE INTO peer_deliveries (id, room, thread, from_person,"
                " to_person, to_agent, payload, status, attempts, created, updated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)",
                (
                    delivery_id,
                    room,
                    thread,
                    from_person,
                    to_person,
                    to_agent,
                    json.dumps(payload),
                    ts,
                    ts,
                ),
            )
        return cur.rowcount == 1

    def get(self, room: str, delivery_id: str) -> PeerDelivery | None:
        with self._core.lock:
            r = self._core.db.execute(
                "SELECT * FROM peer_deliveries WHERE room = ? AND id = ?", (room, delivery_id)
            ).fetchone()
        return None if r is None else _delivery(r)

    def claim_next(self, lease_s: float = 300.0, now: float | None = None) -> PeerDelivery | None:
        """Claim the oldest queued delivery for ``lease_s`` seconds, or None. Two workers never
        get the same delivery."""
        ts = time.time() if now is None else now
        with self._core.write() as db:
            r = db.execute(
                "SELECT * FROM peer_deliveries WHERE status = 'queued'"
                " ORDER BY created, rowid LIMIT 1"
            ).fetchone()
            if r is None:
                return None
            db.execute(
                "UPDATE peer_deliveries SET status = 'leased', attempts = attempts + 1,"
                " lease_until = ?, updated = ? WHERE room = ? AND id = ?",
                (ts + lease_s, ts, r["room"], r["id"]),
            )
            claimed = db.execute(
                "SELECT * FROM peer_deliveries WHERE room = ? AND id = ?", (r["room"], r["id"])
            ).fetchone()
        return _delivery(claimed)

    def done(self, room: str, delivery_id: str, now: float | None = None) -> bool:
        """Mark a leased delivery delivered. False if it was not leased."""
        return self._finish(room, delivery_id, "done", None, now)

    def fail(
        self,
        room: str,
        delivery_id: str,
        reason: str,
        now: float | None = None,
        *,
        undeliverable: bool = False,
    ) -> bool:
        """End a leased delivery with a reason. ``undeliverable`` marks a recipient that cannot
        answer at all (removed, or its owner's model is not connected)."""
        return self._finish(
            room, delivery_id, "undeliverable" if undeliverable else "failed", reason, now
        )

    def _finish(
        self,
        room: str,
        delivery_id: str,
        status: DeliveryStatus,
        reason: str | None,
        now: float | None,
    ) -> bool:
        ts = time.time() if now is None else now
        with self._core.lock:
            cur = self._core.db.execute(
                "UPDATE peer_deliveries SET status = ?, reason = ?, lease_until = NULL,"
                " updated = ? WHERE room = ? AND id = ? AND status = 'leased'",
                (status, reason, ts, room, delivery_id),
            )
        return cur.rowcount == 1

    def requeue_expired(self, now: float | None = None, max_attempts: int = 5) -> int:
        """Requeue every delivery whose lease has run out (the worker died, or the process
        restarted). A delivery that has been tried ``max_attempts`` times fails instead. Returns
        how many were requeued."""
        ts = time.time() if now is None else now
        with self._core.write() as db:
            db.execute(
                "UPDATE peer_deliveries SET status = 'failed', lease_until = NULL, updated = ?,"
                " reason = 'delivery was tried too many times' WHERE status = 'leased'"
                " AND lease_until <= ? AND attempts >= ?",
                (ts, ts, max_attempts),
            )
            cur = db.execute(
                "UPDATE peer_deliveries SET status = 'queued', lease_until = NULL, updated = ?"
                " WHERE status = 'leased' AND lease_until <= ?",
                (ts, ts),
            )
        return cur.rowcount

    def for_room(self, room: str) -> list[PeerDelivery]:
        with self._core.lock:
            rows = self._core.db.execute(
                "SELECT * FROM peer_deliveries WHERE room = ? ORDER BY created, rowid", (room,)
            ).fetchall()
        return [_delivery(r) for r in rows]

    def purge_person(self, person: str, room: str | None = None) -> int:
        """Drop every delivery to or from a person, in one room or in every room."""
        with self._core.lock:
            if room is None:
                cur = self._core.db.execute(
                    "DELETE FROM peer_deliveries WHERE from_person = ? OR to_person = ?",
                    (person, person),
                )
            else:
                cur = self._core.db.execute(
                    "DELETE FROM peer_deliveries WHERE room = ?"
                    " AND (from_person = ? OR to_person = ?)",
                    (room, person, person),
                )
        return cur.rowcount

    def purge_room(self, room: str) -> int:
        with self._core.lock:
            cur = self._core.db.execute("DELETE FROM peer_deliveries WHERE room = ?", (room,))
        return cur.rowcount

    def purge_finished_older_than(self, cutoff_ts: float) -> int:
        """Delete delivered, failed and undeliverable rows last touched before ``cutoff_ts``."""
        with self._core.lock:
            cur = self._core.db.execute(
                "DELETE FROM peer_deliveries WHERE status NOT IN ('queued', 'leased')"
                " AND updated < ?",
                (cutoff_ts,),
            )
        return cur.rowcount
