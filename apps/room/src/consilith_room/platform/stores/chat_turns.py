"""``chat_turns``: the durable chat history, one row per turn, per person, per room.

Unique on ``(room, person, msg_id)``, indexed on ``(room, person, seq)``. Reads and writes are
keyed by the authenticated person only. The caller passes text through ``journal.redact`` before
it is stored (the platform layer cannot import the protocol layer).
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from consilith_contracts.chat import ChatTurn, TurnRole, TurnStatus

from . import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_turns (
  room TEXT NOT NULL, person TEXT NOT NULL, agent TEXT NOT NULL, seq INTEGER NOT NULL,
  ts REAL NOT NULL, role TEXT NOT NULL, run_id TEXT, msg_id TEXT NOT NULL,
  status TEXT NOT NULL, taint INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}',
  UNIQUE (room, person, msg_id)
);
CREATE INDEX IF NOT EXISTS chat_turns_seq ON chat_turns (room, person, seq);
CREATE INDEX IF NOT EXISTS chat_turns_ts ON chat_turns (ts);
"""

_UNSET: Any = object()


def _turn(r: sqlite3.Row) -> ChatTurn:
    return ChatTurn(
        room=r["room"],
        person=r["person"],
        agent=r["agent"],
        seq=int(r["seq"]),
        ts=float(r["ts"]),
        role=TurnRole(r["role"]),
        run_id=r["run_id"],
        msg_id=r["msg_id"],
        status=TurnStatus(r["status"]),
        taint=bool(r["taint"]),
        text=r["text"],
        meta=json.loads(r["meta"]),
    )


class ChatTurns:
    def __init__(self, core: Db) -> None:
        self._core = core

    def admit(self, turn: ChatTurn) -> tuple[bool, ChatTurn]:
        """Atomically record a turn. Returns ``(True, row)`` when this call admitted it, or
        ``(False, existing)`` when ``(room, person, msg_id)`` was already there: exactly one of
        two concurrent calls on one ``msg_id`` admits. ``seq`` and ``ts`` are assigned here."""
        with self._core.write() as db:
            found = db.execute(
                "SELECT * FROM chat_turns WHERE room = ? AND person = ? AND msg_id = ?",
                (turn.room, turn.person, turn.msg_id),
            ).fetchone()
            if found is not None:
                return False, _turn(found)
            (last,) = db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM chat_turns WHERE room = ? AND person = ?",
                (turn.room, turn.person),
            ).fetchone()
            row = turn.model_copy(update={"seq": int(last) + 1, "ts": time.time()})
            db.execute(
                "INSERT INTO chat_turns (room, person, agent, seq, ts, role, run_id, msg_id,"
                " status, taint, text, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.room,
                    row.person,
                    row.agent,
                    row.seq,
                    row.ts,
                    row.role.value,
                    row.run_id,
                    row.msg_id,
                    row.status.value,
                    int(row.taint),
                    row.text,
                    json.dumps(row.meta),
                ),
            )
        return True, row

    def get(self, room: str, person: str, msg_id: str) -> ChatTurn | None:
        with self._core.lock:
            r = self._core.db.execute(
                "SELECT * FROM chat_turns WHERE room = ? AND person = ? AND msg_id = ?",
                (room, person, msg_id),
            ).fetchone()
        return None if r is None else _turn(r)

    def transition(
        self,
        room: str,
        person: str,
        msg_id: str,
        expected: TurnStatus,
        new: TurnStatus,
        *,
        text: str | None = None,
        meta: dict[str, Any] | None = None,
        run_id: str | None = _UNSET,
        taint: bool | None = None,
    ) -> bool:
        """Move a turn from ``expected`` to ``new``. Returns whether this call did it: it is
        False when the turn is missing or no longer in ``expected``. ``text``, ``meta``,
        ``run_id`` and ``taint`` are updated in the same statement when given."""
        sets = ["status = ?"]
        args: list[Any] = [new.value]
        if text is not None:
            sets.append("text = ?")
            args.append(text)
        if meta is not None:
            sets.append("meta = ?")
            args.append(json.dumps(meta))
        if run_id is not _UNSET:
            sets.append("run_id = ?")
            args.append(run_id)
        if taint is not None:
            sets.append("taint = ?")
            args.append(int(taint))
        args += [room, person, msg_id, expected.value]
        with self._core.lock:
            cur = self._core.db.execute(
                f"UPDATE chat_turns SET {', '.join(sets)}"
                " WHERE room = ? AND person = ? AND msg_id = ? AND status = ?",
                args,
            )
        return cur.rowcount == 1

    def last(self, room: str, person: str, n: int) -> list[ChatTurn]:
        """The last ``n`` turns of this person in this room, oldest first."""
        if n <= 0:
            return []
        with self._core.lock:
            rows = self._core.db.execute(
                "SELECT * FROM chat_turns WHERE room = ? AND person = ? ORDER BY seq DESC LIMIT ?",
                (room, person, n),
            ).fetchall()
        return [_turn(r) for r in reversed(rows)]

    def count(self, room: str, person: str) -> int:
        with self._core.lock:
            (n,) = self._core.db.execute(
                "SELECT COUNT(*) FROM chat_turns WHERE room = ? AND person = ?", (room, person)
            ).fetchone()
        return int(n)

    def trim(self, room: str, person: str, keep: int = 500) -> int:
        """Keep only the newest ``keep`` turns of this person in this room."""
        with self._core.lock:
            cur = self._core.db.execute(
                "DELETE FROM chat_turns WHERE room = ? AND person = ? AND seq <="
                " (SELECT COALESCE(MAX(seq), 0) FROM chat_turns WHERE room = ? AND person = ?)"
                " - ?",
                (room, person, room, person, max(keep, 0)),
            )
        return cur.rowcount

    def purge_person(self, person: str, room: str | None = None) -> int:
        """Delete a person's turns, in one room or in every room."""
        with self._core.lock:
            if room is None:
                cur = self._core.db.execute("DELETE FROM chat_turns WHERE person = ?", (person,))
            else:
                cur = self._core.db.execute(
                    "DELETE FROM chat_turns WHERE person = ? AND room = ?", (person, room)
                )
        return cur.rowcount

    def purge_room(self, room: str) -> int:
        with self._core.lock:
            cur = self._core.db.execute("DELETE FROM chat_turns WHERE room = ?", (room,))
        return cur.rowcount

    def purge_older_than(self, cutoff_ts: float) -> int:
        """Delete every turn made before ``cutoff_ts`` (epoch seconds), in every room."""
        with self._core.lock:
            cur = self._core.db.execute("DELETE FROM chat_turns WHERE ts < ?", (cutoff_ts,))
        return cur.rowcount
