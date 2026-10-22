"""SQLite storage behind small methods, so Postgres can replace it later.

The room log is the source of truth: every change is committed here before it is applied to
the in-memory document (commit before apply). ``seq`` is per room; ``(room, op_id)`` is unique,
so a retried operation is recognised and dropped.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .stores import (
    PERSON_TABLES,
    ROOM_TABLES,
    Db,
    register_leave_hook,
    register_room_delete_hook,
    run_leave_hooks,
    run_room_delete_hooks,
    unregister_leave_hook,
    unregister_room_delete_hook,
)
from .stores import chat_turns as _chat_turns
from .stores import peer_deliveries as _peer_deliveries
from .stores import peer_messages as _peer_messages
from .stores import pending_actions as _pending_actions

__all__ = [
    "DuplicateOp",
    "LogRow",
    "Store",
    "register_leave_hook",
    "register_room_delete_hook",
    "run_leave_hooks",
    "run_room_delete_hooks",
    "unregister_leave_hook",
    "unregister_room_delete_hook",
]

JOURNAL_PAGE = 5000

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS rooms (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS log (
  room TEXT NOT NULL, seq INTEGER NOT NULL, op_id TEXT NOT NULL, ts REAL NOT NULL,
  actor TEXT NOT NULL, kind TEXT NOT NULL, patch TEXT NOT NULL, meta TEXT NOT NULL,
  PRIMARY KEY (room, seq), UNIQUE (room, op_id)
);
CREATE TABLE IF NOT EXISTS journal (
  room TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL, entry TEXT NOT NULL,
  PRIMARY KEY (room, seq)
);
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, email TEXT UNIQUE, github_id TEXT UNIQUE,
  name TEXT NOT NULL, color TEXT NOT NULL, discipline TEXT NOT NULL,
  profile_complete INTEGER NOT NULL, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS one_time (
  digest TEXT PRIMARY KEY, purpose TEXT NOT NULL, subject TEXT NOT NULL, data TEXT NOT NULL,
  expires REAL NOT NULL, used_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
  sid TEXT PRIMARY KEY, person TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
  revoked_at REAL
);
CREATE TABLE IF NOT EXISTS invites (
  id TEXT PRIMARY KEY, room TEXT NOT NULL, kind TEXT NOT NULL, email TEXT,
  digest TEXT NOT NULL UNIQUE, created_by TEXT NOT NULL, created REAL NOT NULL,
  expires REAL NOT NULL, revoked_at REAL, used_at REAL, used_by TEXT
);
CREATE TABLE IF NOT EXISTS seats (
  room TEXT NOT NULL, person TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY (room, person)
);
CREATE TABLE IF NOT EXISTS agent_prefs (person TEXT PRIMARY KEY, prefs TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS person_memory (
  person TEXT NOT NULL, id TEXT NOT NULL, lesson TEXT NOT NULL, ts REAL NOT NULL,
  PRIMARY KEY (person, id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  sha256 TEXT PRIMARY KEY, media_type TEXT NOT NULL, data BLOB NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS manifests (
  digest TEXT PRIMARY KEY, manifest TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, room TEXT NOT NULL, status TEXT NOT NULL, spec TEXT NOT NULL,
  result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL
);
""" + "".join(
    (
        _chat_turns.SCHEMA,
        _pending_actions.SCHEMA,
        _peer_deliveries.SCHEMA,
        _peer_messages.SCHEMA,
    )
)

# The journal keeps these fields only inside its JSON entry. Virtual generated columns expose
# them to an index without rewriting a row, so an existing database gains them in place.
JOURNAL_COLUMNS = (
    ("correlation_id", "$.correlationId"),
    ("viewer", "$.viewer"),
    ("run_id", "$.refs.runId"),
)
JOURNAL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS journal_correlation ON journal (room, correlation_id)",
    "CREATE INDEX IF NOT EXISTS journal_viewer_run ON journal (room, viewer, run_id)",
)


@dataclass(frozen=True)
class LogRow:
    room: str
    seq: int
    op_id: str
    ts: float
    actor: str
    kind: str
    patch: list[dict[str, Any]]
    meta: dict[str, Any]


class DuplicateOp(Exception):
    """The (room, op_id) pair was already committed. Carries the original row."""

    def __init__(self, row: LogRow) -> None:
        super().__init__(f"duplicate op {row.op_id} in room {row.room} (seq {row.seq})")
        self.row = row


class Store:
    """Thread-safe SQLite store. One connection, one lock: writes are small and fast."""

    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._core = Db(self._db, self._lock)
        with self._lock:
            self._db.executescript(SCHEMA)
            self._ensure_journal_index()
        self.chat_turns = _chat_turns.ChatTurns(self._core)
        self.pending_actions = _pending_actions.PendingActions(self._core)
        self.peer_deliveries = _peer_deliveries.PeerDeliveries(self._core)
        self.peer_messages = _peer_messages.PeerMessages(self._core)

    def _ensure_journal_index(self) -> None:
        have = {str(r["name"]) for r in self._db.execute("PRAGMA table_xinfo(journal)")}
        for name, path in JOURNAL_COLUMNS:
            if name not in have:
                self._db.execute(
                    f"ALTER TABLE journal ADD COLUMN {name} TEXT"
                    f" GENERATED ALWAYS AS (json_extract(entry, '{path}')) VIRTUAL"
                )
        for ddl in JOURNAL_INDEXES:
            self._db.execute(ddl)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # rooms -----------------------------------------------------------------------------
    def create_room(self, room: str, title: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO rooms (id, title, created) VALUES (?, ?, ?)",
                (room, title, time.time()),
            )

    def rooms(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._db.execute("SELECT id, title FROM rooms ORDER BY created").fetchall()
        return [(r["id"], r["title"]) for r in rows]

    # log -------------------------------------------------------------------------------
    def append(
        self,
        room: str,
        op_id: str,
        actor: str,
        kind: str,
        patch: list[dict[str, Any]],
        meta: dict[str, Any],
        lessons: list[tuple[str, str, dict[str, Any]]] | None = None,
    ) -> LogRow:
        """Commit one change. Raises DuplicateOp if this op_id was already committed.

        ``lessons`` (person, id, lesson) are written in the same transaction, so a decision
        and the lesson it teaches are committed together or not at all (FR-MEM-1)."""
        ts = time.time()
        with self._lock:
            existing = self._db.execute(
                "SELECT * FROM log WHERE room = ? AND op_id = ?", (room, op_id)
            ).fetchone()
            if existing is not None:
                raise DuplicateOp(_row(existing))
            self._db.execute("BEGIN IMMEDIATE")
            try:
                (last,) = self._db.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM log WHERE room = ?", (room,)
                ).fetchone()
                seq = int(last) + 1
                self._db.execute(
                    "INSERT INTO log (room, seq, op_id, ts, actor, kind, patch, meta)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (room, seq, op_id, ts, actor, kind, json.dumps(patch), json.dumps(meta)),
                )
                for person, lesson_id, lesson in lessons or []:
                    self._db.execute(
                        "INSERT INTO person_memory (person, id, lesson, ts) VALUES (?, ?, ?, ?)"
                        " ON CONFLICT(person, id) DO UPDATE SET lesson=excluded.lesson",
                        (person, lesson_id, json.dumps(lesson), ts),
                    )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return LogRow(room, seq, op_id, ts, actor, kind, patch, meta)

    def find_op(self, room: str, op_id: str) -> LogRow | None:
        with self._lock:
            r = self._db.execute(
                "SELECT * FROM log WHERE room = ? AND op_id = ?", (room, op_id)
            ).fetchone()
        return None if r is None else _row(r)

    def log(self, room: str, after: int = 0, limit: int = 10_000) -> list[LogRow]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM log WHERE room = ? AND seq > ? ORDER BY seq LIMIT ?",
                (room, after, limit),
            ).fetchall()
        return [_row(r) for r in rows]

    def last_seq(self, room: str) -> int:
        with self._lock:
            (last,) = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM log WHERE room = ?", (room,)
            ).fetchone()
        return int(last)

    # protocol journal ------------------------------------------------------------------
    def journal_append(
        self,
        room: str,
        entry: dict[str, Any],
        build: Callable[[int], dict[str, Any]] | None = None,
    ) -> int:
        """Append one entry. ``build``, when given, makes the payload from the entry's own seq
        inside the same transaction (a message that names itself, such as A2UI wiring)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                (last,) = self._db.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM journal WHERE room = ?", (room,)
                ).fetchone()
                seq = int(last) + 1
                entry = {**entry, "seq": seq}
                if build is not None:
                    entry["payload"] = build(seq)
                self._db.execute(
                    "INSERT INTO journal (room, seq, ts, entry) VALUES (?, ?, ?, ?)",
                    (room, seq, float(entry.get("ts", time.time())), json.dumps(entry)),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return seq

    def journal_last_seq(self, room: str) -> int:
        with self._lock:
            (last,) = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM journal WHERE room = ?", (room,)
            ).fetchone()
        return int(last)

    def journal(self, room: str, after: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        """Entries after ``after``, oldest first. With no ``limit`` that is every entry, read in
        pages, so a long journal is never cut short."""
        if limit is not None:
            return self.journal_page(room, after, limit)
        return list(self.journal_iter(room, after))

    def journal_page(
        self, room: str, after: int = 0, limit: int = JOURNAL_PAGE
    ) -> list[dict[str, Any]]:
        """At most ``limit`` entries with seq > ``after``. Continue from the last entry's seq."""
        with self._lock:
            rows = self._db.execute(
                "SELECT entry FROM journal WHERE room = ? AND seq > ? ORDER BY seq LIMIT ?",
                (room, after, limit),
            ).fetchall()
        return [json.loads(r["entry"]) for r in rows]

    def journal_iter(
        self, room: str, after: int = 0, page: int = JOURNAL_PAGE
    ) -> Iterator[dict[str, Any]]:
        """Every entry after ``after``, in pages, holding the lock only while a page is read."""
        page = max(page, 1)
        while True:
            entries = self.journal_page(room, after, page)
            yield from entries
            if len(entries) < page:
                return
            after = int(entries[-1]["seq"])

    def journal_by_correlation(self, room: str, correlation_id: str) -> list[dict[str, Any]]:
        """Entries that share a correlation id (a tool call's start, progress and end), through
        the index and not a scan."""
        with self._lock:
            rows = self._db.execute(
                "SELECT entry FROM journal WHERE room = ? AND correlation_id = ? ORDER BY seq",
                (room, correlation_id),
            ).fetchall()
        return [json.loads(r["entry"]) for r in rows]

    def journal_by_run(self, room: str, viewer: str, run_id: str) -> list[dict[str, Any]]:
        """One viewer's entries for one run, through the index."""
        with self._lock:
            rows = self._db.execute(
                "SELECT entry FROM journal WHERE room = ? AND viewer = ? AND run_id = ?"
                " ORDER BY seq",
                (room, viewer, run_id),
            ).fetchall()
        return [json.loads(r["entry"]) for r in rows]

    def journal_purge_before(self, cutoff_ts: float, protocol: str = "AG-UI") -> int:
        """Retention for chat events: delete ``protocol`` entries older than ``cutoff_ts``."""
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM journal WHERE ts < ? AND json_extract(entry, '$.protocol') = ?",
                (cutoff_ts, protocol),
            )
        return cur.rowcount

    # accounts (real people, outside any room) --------------------------------------------
    def create_account(
        self,
        base_id: str,
        *,
        email: str | None = None,
        github_id: str | None = None,
        name: str = "",
        color: str = "blue",
        reserved: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Create an account, or return the one that already has this email or GitHub id.
        The id is ``base_id``, then ``base_id-2``, ``-3``..., chosen inside one transaction."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                found = None
                if email is not None:
                    found = self._db.execute(
                        "SELECT * FROM accounts WHERE email = ?", (email,)
                    ).fetchone()
                if found is None and github_id is not None:
                    found = self._db.execute(
                        "SELECT * FROM accounts WHERE github_id = ?", (github_id,)
                    ).fetchone()
                if found is None:
                    n = 1
                    while True:
                        cand = base_id if n == 1 else f"{base_id}-{n}"
                        taken = (
                            cand in reserved
                            or self._db.execute(
                                "SELECT 1 FROM accounts WHERE id = ?", (cand,)
                            ).fetchone()
                        )
                        if not taken:
                            break
                        n += 1
                    self._db.execute(
                        "INSERT INTO accounts (id, email, github_id, name, color, discipline,"
                        " profile_complete, created) VALUES (?, ?, ?, ?, ?, 'none', 0, ?)",
                        (cand, email, github_id, name, color, time.time()),
                    )
                    found = self._db.execute(
                        "SELECT * FROM accounts WHERE id = ?", (cand,)
                    ).fetchone()
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return _account(found)

    def person(self, pid: str) -> dict[str, Any] | None:
        return self._account_row("SELECT * FROM accounts WHERE id = ?", (pid,))

    def person_by_email(self, email: str) -> dict[str, Any] | None:
        return self._account_row("SELECT * FROM accounts WHERE email = ?", (email,))

    def person_by_github(self, github_id: str) -> dict[str, Any] | None:
        return self._account_row("SELECT * FROM accounts WHERE github_id = ?", (github_id,))

    def _account_row(self, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute(sql, args).fetchone()
        return None if r is None else _account(r)

    def account_count(self) -> int:
        with self._lock:
            (n,) = self._db.execute("SELECT COUNT(*) FROM accounts").fetchone()
        return int(n)

    def set_profile(self, pid: str, name: str, color: str, discipline: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE accounts SET name = ?, color = ?, discipline = ?, profile_complete = 1"
                " WHERE id = ?",
                (name, color, discipline, pid),
            )

    def link_github(self, pid: str, github_id: str) -> bool:
        """Attach a GitHub id to an account that has none. False if it already has one."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE accounts SET github_id = ? WHERE id = ? AND github_id IS NULL",
                (github_id, pid),
            )
        return cur.rowcount == 1

    # one-time secrets: sign-in links, OAuth state, sign-in tickets -------------------------
    def put_one_time(
        self, digest: str, purpose: str, subject: str, data: dict[str, Any], expires: float
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO one_time (digest, purpose, subject, data, expires) VALUES (?,?,?,?,?)",
                (digest, purpose, subject, json.dumps(data), expires),
            )

    def consume_one_time(self, digest: str, purpose: str) -> tuple[str, dict[str, Any]] | None:
        """Redeem once. The check and the mark are one statement, so two concurrent redemptions
        cannot both succeed."""
        now = time.time()
        with self._lock:
            r = self._db.execute(
                "UPDATE one_time SET used_at = ? WHERE digest = ? AND purpose = ?"
                " AND used_at IS NULL AND expires > ? RETURNING subject, data",
                (now, digest, purpose, now),
            ).fetchone()
        return None if r is None else (str(r["subject"]), json.loads(r["data"]))

    # sessions --------------------------------------------------------------------------
    def create_session(self, sid: str, person: str, ttl_s: float) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO sessions (sid, person, created, expires) VALUES (?, ?, ?, ?)",
                (sid, person, now, now + ttl_s),
            )

    def session_live(self, sid: str, person: str) -> bool:
        with self._lock:
            r = self._db.execute(
                "SELECT 1 FROM sessions WHERE sid = ? AND person = ? AND revoked_at IS NULL"
                " AND expires > ?",
                (sid, person, time.time()),
            ).fetchone()
        return r is not None

    def revoke_session(self, sid: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sessions SET revoked_at = ? WHERE sid = ? AND revoked_at IS NULL",
                (time.time(), sid),
            )

    def live_sessions(self, person: str) -> list[str]:
        """Live session ids of a person, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT sid FROM sessions WHERE person = ? AND revoked_at IS NULL AND expires > ?"
                " ORDER BY created DESC, rowid DESC",
                (person, time.time()),
            ).fetchall()
        return [str(r["sid"]) for r in rows]

    # seats (a copy of the room documents' people, to list a person's rooms) -------------------
    def add_seat(self, room: str, person: str, role: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO seats (room, person, role) VALUES (?, ?, ?)",
                (room, person, role),
            )

    def seats_of(self, person: str) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT s.room, s.role FROM seats s JOIN rooms r ON r.id = s.room"
                " WHERE s.person = ? ORDER BY r.created",
                (person,),
            ).fetchall()
        return [(str(r["room"]), str(r["role"])) for r in rows]

    def remove_seat(self, room: str, person: str) -> None:
        """Remove the seat and the person's data in this room (chat turns, confirm cards and
        peer traffic), in one transaction. The leave hooks are run by ``Admission.leave``."""
        with self._core.write() as db:
            db.execute("DELETE FROM seats WHERE room = ? AND person = ?", (room, person))
            for table, columns in PERSON_TABLES:
                match = " OR ".join(f"{c} = ?" for c in columns)
                db.execute(
                    f"DELETE FROM {table} WHERE room = ? AND ({match})",
                    (room, *([person] * len(columns))),
                )

    def room_owner(self, room: str) -> str | None:
        """Who created the room: the maker of its first invite, the room link made at creation."""
        with self._lock:
            row = self._db.execute(
                "SELECT created_by FROM invites WHERE room = ? ORDER BY created, rowid LIMIT 1",
                (room,),
            ).fetchone()
        return str(row["created_by"]) if row else None

    def delete_room(self, room: str, *, run_hooks: bool = True) -> None:
        """Everything this room holds: its document history, journal, seats, invites, jobs, chat
        turns, confirm cards and peer traffic. Content-addressed artifacts are shared and stay.
        One transaction: all or nothing. The delete hooks run after it, unless ``run_hooks`` is
        False (``Admission.delete`` runs them itself)."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for table in ("log", "journal", "seats", "invites", "jobs", *ROOM_TABLES):
                    self._db.execute(f"DELETE FROM {table} WHERE room = ?", (room,))
                self._db.execute("DELETE FROM rooms WHERE id = ?", (room,))
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")
        if run_hooks:
            run_room_delete_hooks(self, room)

    def seat_people(self, room: str) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT person FROM seats WHERE room = ?", (room,)).fetchall()
        return [str(r["person"]) for r in rows]

    # invites ---------------------------------------------------------------------------
    def add_invite(
        self,
        invite_id: str,
        room: str,
        kind: str,
        email: str | None,
        digest: str,
        created_by: str,
        expires: float,
    ) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO invites (id, room, kind, email, digest, created_by, created, expires)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (invite_id, room, kind, email, digest, created_by, time.time(), expires),
            )

    def invite_by_digest(self, digest: str) -> dict[str, Any] | None:
        return self._invite_row("SELECT * FROM invites WHERE digest = ?", (digest,))

    def invite(self, invite_id: str) -> dict[str, Any] | None:
        return self._invite_row("SELECT * FROM invites WHERE id = ?", (invite_id,))

    def _invite_row(self, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute(sql, args).fetchone()
        return None if r is None else {k: r[k] for k in r.keys()}  # noqa: SIM118

    def pending_email_invites(self, email: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM invites WHERE kind = 'email' AND email = ? AND used_at IS NULL"
                " AND revoked_at IS NULL AND expires > ? ORDER BY created",
                (email, time.time()),
            ).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]  # noqa: SIM118

    def use_invite(self, invite_id: str, person: str, single_use: bool) -> bool:
        """Spend an invite. An email invite is spent once, in one statement; a room link stays
        valid until it expires or is revoked. False if it can no longer be used."""
        now = time.time()
        with self._lock:
            if single_use:
                cur = self._db.execute(
                    "UPDATE invites SET used_at = ?, used_by = ? WHERE id = ? AND used_at IS NULL"
                    " AND revoked_at IS NULL AND expires > ?",
                    (now, person, invite_id, now),
                )
            else:
                cur = self._db.execute(
                    "UPDATE invites SET used_by = ? WHERE id = ? AND revoked_at IS NULL"
                    " AND expires > ?",
                    (person, invite_id, now),
                )
        return cur.rowcount == 1

    def release_invite(self, invite_id: str) -> None:
        """Undo ``use_invite`` of an email invite whose seat could not be given."""
        with self._lock:
            self._db.execute(
                "UPDATE invites SET used_at = NULL, used_by = NULL WHERE id = ?", (invite_id,)
            )

    def revoke_invite(self, invite_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE invites SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (time.time(), invite_id),
            )

    # the agent a person brings to every room, chosen once (M9 "Your agent") ----------------------
    def agent_prefs(self, person: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute(
                "SELECT prefs FROM agent_prefs WHERE person = ?", (person,)
            ).fetchone()
        return None if r is None else json.loads(r["prefs"])

    def put_agent_prefs(self, person: str, prefs: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO agent_prefs (person, prefs) VALUES (?, ?)"
                " ON CONFLICT(person) DO UPDATE SET prefs = excluded.prefs",
                (person, json.dumps(prefs)),
            )

    # person memory (outside any room, FR-MEM-5) -------------------------------------------
    def put_lesson(self, person: str, lesson_id: str, lesson: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO person_memory (person, id, lesson, ts) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(person, id) DO UPDATE SET lesson=excluded.lesson, ts=excluded.ts",
                (person, lesson_id, json.dumps(lesson), time.time()),
            )

    def lessons(self, person: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT lesson FROM person_memory WHERE person = ? ORDER BY ts", (person,)
            ).fetchall()
        return [json.loads(r["lesson"]) for r in rows]

    # content-addressed artifacts and run manifests -------------------------------------
    def put_artifact(self, sha256: str, media_type: str, data: bytes) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO artifacts (sha256, media_type, data, ts)"
                " VALUES (?, ?, ?, ?)",
                (sha256, media_type, data, time.time()),
            )

    def artifact(self, sha256: str) -> tuple[str, bytes] | None:
        with self._lock:
            r = self._db.execute(
                "SELECT media_type, data FROM artifacts WHERE sha256 = ?", (sha256,)
            ).fetchone()
        return None if r is None else (str(r["media_type"]), bytes(r["data"]))

    def put_manifest(self, digest: str, manifest: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO manifests (digest, manifest, ts) VALUES (?, ?, ?)",
                (digest, json.dumps(manifest), time.time()),
            )

    def manifest(self, digest: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._db.execute(
                "SELECT manifest FROM manifests WHERE digest = ?", (digest,)
            ).fetchone()
        return None if r is None else json.loads(r["manifest"])

    # tier-2 jobs -----------------------------------------------------------------------
    def put_job(self, job_id: str, room: str, status: str, spec: dict[str, Any]) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO jobs (id, room, status, spec, created, updated)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, room, status, json.dumps(spec), now, now),
            )

    def update_job(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE jobs SET status = ?, result = ?, error = ?, updated = ? WHERE id = ?",
                (
                    status,
                    None if result is None else json.dumps(result),
                    error,
                    time.time(),
                    job_id,
                ),
            )

    def jobs(self, status: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM jobs" + (" WHERE status = ?" if status else "") + " ORDER BY created"
        with self._lock:
            rows = self._db.execute(q, (status,) if status else ()).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "room": r["room"],
                    "status": r["status"],
                    "spec": json.loads(r["spec"]),
                    "result": None if r["result"] is None else json.loads(r["result"]),
                    "error": r["error"],
                }
            )
        return out


def _account(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "email": r["email"],
        "github_id": r["github_id"],
        "name": r["name"],
        "color": r["color"],
        "discipline": r["discipline"],
        "profile_complete": bool(r["profile_complete"]),
    }


def _row(r: sqlite3.Row) -> LogRow:
    return LogRow(
        room=r["room"],
        seq=int(r["seq"]),
        op_id=r["op_id"],
        ts=float(r["ts"]),
        actor=r["actor"],
        kind=r["kind"],
        patch=json.loads(r["patch"]),
        meta=json.loads(r["meta"]),
    )
