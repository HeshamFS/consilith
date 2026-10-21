"""What each agent is doing right now, shown to everyone in the room (FR-AG-3, FR-MP-9).

``acting`` marks a step for its whole length. Steps can overlap (two solves, a chat during a
solve), so each agent keeps a stack of its running steps: it shows the newest one still
running, and when the last one ends it comes to rest. At rest an agent is ``paused`` while a
proposal of its own awaits its owner, else ``idle``; proposals and decisions set that in their
own commits.
"""

from __future__ import annotations

import threading
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..domain.errors import NotFound
from ..domain.service import RoomService

_steps: weakref.WeakKeyDictionary[RoomService, dict[str, list[tuple[object, str, str]]]] = (
    weakref.WeakKeyDictionary()
)
_lock = threading.Lock()


def _show(room: RoomService, aid: str) -> None:
    with _lock:
        running = list(_steps.get(room, {}).get(aid, []))
    if running:
        _, status, detail = running[-1]
        room.set_agent_status(aid, status, detail)
    else:
        room.settle_agent_status(aid)


@asynccontextmanager
async def acting(room: RoomService, aid: str, status: str, detail: str = "") -> AsyncIterator[None]:
    try:
        room.agent(aid)
    except NotFound:  # not a member of this room's document (for example the referee)
        yield
        return
    token = object()
    with _lock:
        _steps.setdefault(room, {}).setdefault(aid, []).append((token, status, detail))
    _show(room, aid)
    try:
        yield
    finally:
        with _lock:
            stack = _steps.get(room, {}).get(aid, [])
            stack[:] = [s for s in stack if s[0] is not token]
        _show(room, aid)


__all__ = ["acting"]
