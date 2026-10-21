"""In-process room bus: wakes subscribers when a room changes.

The bus carries no data and no order. A subscriber that wakes reads the durable log (or the
journal) from its own cursor, so a missed or coalesced wake-up loses nothing.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Any


class RoomBus:
    def __init__(self) -> None:
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._ephemeral: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, room: str) -> asyncio.Event:
        ev = asyncio.Event()
        self._waiters[room].add(ev)
        return ev

    def unsubscribe(self, room: str, ev: asyncio.Event) -> None:
        self._waiters[room].discard(ev)

    def notify(self, room: str) -> None:
        """Safe to call from any thread."""
        waiters = list(self._waiters.get(room, ()))
        if not waiters:
            return
        loop = self._loop
        for ev in waiters:
            if loop is not None and loop.is_running():
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if running is loop:
                    ev.set()
                else:
                    loop.call_soon_threadsafe(ev.set)
            else:
                ev.set()

    # Presence is ephemeral: it never enters the log, so it travels as callbacks.
    def on_ephemeral(self, room: str, fn: Callable[[dict[str, Any]], None]) -> None:
        self._ephemeral[room].append(fn)

    def off_ephemeral(self, room: str, fn: Callable[[dict[str, Any]], None]) -> None:
        if fn in self._ephemeral.get(room, []):
            self._ephemeral[room].remove(fn)

    def publish_ephemeral(self, room: str, event: dict[str, Any]) -> None:
        for fn in list(self._ephemeral.get(room, ())):
            fn(event)
