"""The agent platform's tables: chat turns, pending actions, peer deliveries and peer messages.

Each store shares the connection and the lock of ``Store`` (one connection, one lock), so every
write follows the same discipline as the rest of the store. The schemas are created
idempotently with ``CREATE TABLE IF NOT EXISTS``, so an existing database gains them when it is
opened and needs no migration step.

This module also holds the leave and room-delete hook registry. Hooks registered by other
packages run after the store's own purge, and a hook that raises is logged and skipped, so the
others still run.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import Store

logger = logging.getLogger("consilith.store")

LeaveHook = Callable[["Store", str, str], None]  # (store, room, person)
RoomDeleteHook = Callable[["Store", str], None]  # (store, room)

_leave_hooks: list[LeaveHook] = []
_room_delete_hooks: list[RoomDeleteHook] = []


def register_leave_hook(fn: LeaveHook) -> LeaveHook:
    """Run ``fn(store, room, person)`` when a person leaves a room. Registering twice is one
    registration. Returns ``fn`` so it can be used as a decorator."""
    if fn not in _leave_hooks:
        _leave_hooks.append(fn)
    return fn


def register_room_delete_hook(fn: RoomDeleteHook) -> RoomDeleteHook:
    """Run ``fn(store, room)`` after a room is deleted. Registering twice is one registration."""
    if fn not in _room_delete_hooks:
        _room_delete_hooks.append(fn)
    return fn


def unregister_leave_hook(fn: LeaveHook) -> None:
    if fn in _leave_hooks:
        _leave_hooks.remove(fn)


def unregister_room_delete_hook(fn: RoomDeleteHook) -> None:
    if fn in _room_delete_hooks:
        _room_delete_hooks.remove(fn)


def run_leave_hooks(store: Store, room: str, person: str) -> int:
    """Call every leave hook. A failing hook is logged and the rest still run. Returns how many
    hooks failed."""
    failed = 0
    for fn in list(_leave_hooks):
        try:
            fn(store, room, person)
        except Exception:
            failed += 1
            logger.exception("leave hook %s failed (room %s)", _name(fn), room)
    return failed


def run_room_delete_hooks(store: Store, room: str) -> int:
    """Call every room-delete hook. A failing hook is logged and the rest still run. Returns how
    many hooks failed."""
    failed = 0
    for fn in list(_room_delete_hooks):
        try:
            fn(store, room)
        except Exception:
            failed += 1
            logger.exception("room delete hook %s failed (room %s)", _name(fn), room)
    return failed


def _name(fn: object) -> str:
    return getattr(fn, "__qualname__", repr(fn))


@dataclass(frozen=True)
class Db:
    """The shared connection and lock of ``Store``."""

    db: sqlite3.Connection
    lock: threading.Lock

    @contextmanager
    def write(self) -> Generator[sqlite3.Connection]:
        """One write transaction under the store lock: all of it or none of it."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")


# The tables that hold a person's data, with the columns that name a person in a row. A row is
# the person's when any of them matches. ``Store.remove_seat`` purges these in the same
# transaction as the seat; a peer row belongs to both its sender and its recipient.
PERSON_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chat_turns", ("person",)),
    ("pending_actions", ("person",)),
    ("peer_deliveries", ("from_person", "to_person")),
    ("peer_messages", ("from_person", "to_person")),
)
# Every new table, for ``Store.delete_room``. All have a ``room`` column.
ROOM_TABLES: tuple[str, ...] = (
    "chat_turns",
    "pending_actions",
    "peer_deliveries",
    "peer_messages",
)
