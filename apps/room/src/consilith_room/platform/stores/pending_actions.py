"""``pending_actions``: confirm cards, private to the person they are for.

Lookup is always by ``(room, person, id)``, so another viewer can never read or resolve a card.
The ``pending -> executing`` move is an atomic compare-and-set, so two tabs approving at once
execute once.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from consilith_contracts.approvals import PendingAction, PendingActionKind, PendingActionStatus
from consilith_contracts.extensions import PackagePin

from . import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_actions (
  id TEXT NOT NULL, room TEXT NOT NULL, person TEXT NOT NULL, msg_id TEXT NOT NULL,
  kind TEXT NOT NULL, call_json TEXT NOT NULL, resolved_args TEXT NOT NULL,
  digest TEXT NOT NULL, input_revisions TEXT NOT NULL, plugin_pins TEXT NOT NULL,
  taint INTEGER NOT NULL DEFAULT 0, origin TEXT NOT NULL, status TEXT NOT NULL,
  result_json TEXT, created REAL NOT NULL, expires REAL NOT NULL,
  PRIMARY KEY (room, person, id)
);
CREATE INDEX IF NOT EXISTS pending_actions_msg ON pending_actions (room, person, msg_id);
CREATE INDEX IF NOT EXISTS pending_actions_expiry ON pending_actions (status, expires);
"""

_PENDING = PendingActionStatus.PENDING.value
_EXECUTING = PendingActionStatus.EXECUTING.value


def _action(r: sqlite3.Row) -> PendingAction:
    return PendingAction(
        id=r["id"],
        room=r["room"],
        person=r["person"],
        msg_id=r["msg_id"],
        kind=PendingActionKind(r["kind"]),
        call=json.loads(r["call_json"]),
        resolved_args=json.loads(r["resolved_args"]),
        digest=r["digest"],
        input_revisions=json.loads(r["input_revisions"]),
        plugin_pins=[PackagePin.model_validate(p) for p in json.loads(r["plugin_pins"])],
        taint=bool(r["taint"]),
        origin=r["origin"],
        status=PendingActionStatus(r["status"]),
        result=None if r["result_json"] is None else json.loads(r["result_json"]),
        created=float(r["created"]),
        expires=float(r["expires"]),
    )


class PendingActions:
    def __init__(self, core: Db) -> None:
        self._core = core

    def add(self, action: PendingAction) -> PendingAction:
        """Store a card. Raises ``sqlite3.IntegrityError`` if ``(room, person, id)`` exists."""
        with self._core.lock:
            self._core.db.execute(
                "INSERT INTO pending_actions (id, room, person, msg_id, kind, call_json,"
                " resolved_args, digest, input_revisions, plugin_pins, taint, origin, status,"
                " result_json, created, expires) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?)",
                (
                    action.id,
                    action.room,
                    action.person,
                    action.msg_id,
                    action.kind.value,
                    json.dumps(action.call),
                    json.dumps(action.resolved_args),
                    action.digest,
                    json.dumps(action.input_revisions),
                    json.dumps([p.wire() for p in action.plugin_pins]),
                    int(action.taint),
                    action.origin,
                    action.status.value,
                    None if action.result is None else json.dumps(action.result),
                    action.created,
                    action.expires,
                ),
            )
        return action

    def get(self, room: str, person: str, action_id: str) -> PendingAction | None:
        """The card, only for the person it belongs to."""
        with self._core.lock:
            r = self._core.db.execute(
                "SELECT * FROM pending_actions WHERE room = ? AND person = ? AND id = ?",
                (room, person, action_id),
            ).fetchone()
        return None if r is None else _action(r)

    def open_for(self, room: str, person: str) -> list[PendingAction]:
        """Cards still awaiting a decision, oldest first."""
        with self._core.lock:
            rows = self._core.db.execute(
                "SELECT * FROM pending_actions WHERE room = ? AND person = ? AND status = ?"
                " ORDER BY created, id",
                (room, person, _PENDING),
            ).fetchall()
        return [_action(r) for r in rows]

    def for_message(self, room: str, person: str, msg_id: str) -> list[PendingAction]:
        with self._core.lock:
            rows = self._core.db.execute(
                "SELECT * FROM pending_actions WHERE room = ? AND person = ? AND msg_id = ?"
                " ORDER BY created, id",
                (room, person, msg_id),
            ).fetchall()
        return [_action(r) for r in rows]

    def transition_pending_to_executing(
        self, room: str, person: str, action_id: str, now: float | None = None
    ) -> bool:
        """Atomic compare-and-set from ``pending`` to ``executing``. Returns whether this call
        won. A card of another person, a card that is no longer pending, and an expired card all
        return False."""
        now = time.time() if now is None else now
        with self._core.lock:
            cur = self._core.db.execute(
                "UPDATE pending_actions SET status = ? WHERE status = ? AND room = ?"
                " AND person = ? AND id = ? AND expires > ?",
                (_EXECUTING, _PENDING, room, person, action_id, now),
            )
        return cur.rowcount == 1

    def set_status(
        self,
        room: str,
        person: str,
        action_id: str,
        expected: PendingActionStatus,
        new: PendingActionStatus,
        result: dict[str, Any] | None = None,
    ) -> bool:
        """Move a card from ``expected`` to ``new`` (``result`` stored when given). Returns
        whether this call did it."""
        with self._core.lock:
            if result is None:
                cur = self._core.db.execute(
                    "UPDATE pending_actions SET status = ? WHERE room = ? AND person = ?"
                    " AND id = ? AND status = ?",
                    (new.value, room, person, action_id, expected.value),
                )
            else:
                cur = self._core.db.execute(
                    "UPDATE pending_actions SET status = ?, result_json = ? WHERE room = ?"
                    " AND person = ? AND id = ? AND status = ?",
                    (new.value, json.dumps(result), room, person, action_id, expected.value),
                )
        return cur.rowcount == 1

    def store_result(
        self,
        room: str,
        person: str,
        action_id: str,
        result: dict[str, Any],
        status: PendingActionStatus = PendingActionStatus.DONE,
    ) -> bool:
        """Finish an ``executing`` card: store its result and set its final status, so a
        repeated resume returns the stored result."""
        return self.set_status(
            room, person, action_id, PendingActionStatus.EXECUTING, status, result
        )

    def supersede_for_message(self, room: str, person: str, msg_id: str) -> int:
        """Supersede every still-pending card of one message. Returns how many."""
        with self._core.lock:
            cur = self._core.db.execute(
                "UPDATE pending_actions SET status = ? WHERE room = ? AND person = ?"
                " AND msg_id = ? AND status = ?",
                (PendingActionStatus.SUPERSEDED.value, room, person, msg_id, _PENDING),
            )
        return cur.rowcount

    def expire_due(self, now: float | None = None) -> int:
        """The expiry sweep: every pending card past its expiry becomes ``expired``."""
        now = time.time() if now is None else now
        with self._core.lock:
            cur = self._core.db.execute(
                "UPDATE pending_actions SET status = ? WHERE status = ? AND expires <= ?",
                (PendingActionStatus.EXPIRED.value, _PENDING, now),
            )
        return cur.rowcount

    def cancel_for_person(self, room: str, person: str) -> int:
        """Cancel this person's pending and executing cards in a room (a leaving person)."""
        with self._core.lock:
            cur = self._core.db.execute(
                "UPDATE pending_actions SET status = ? WHERE room = ? AND person = ?"
                " AND status IN (?, ?)",
                (PendingActionStatus.CANCELLED.value, room, person, _PENDING, _EXECUTING),
            )
        return cur.rowcount

    def purge_person(self, person: str, room: str | None = None) -> int:
        with self._core.lock:
            if room is None:
                cur = self._core.db.execute(
                    "DELETE FROM pending_actions WHERE person = ?", (person,)
                )
            else:
                cur = self._core.db.execute(
                    "DELETE FROM pending_actions WHERE person = ? AND room = ?", (person, room)
                )
        return cur.rowcount

    def purge_room(self, room: str) -> int:
        with self._core.lock:
            cur = self._core.db.execute("DELETE FROM pending_actions WHERE room = ?", (room,))
        return cur.rowcount
