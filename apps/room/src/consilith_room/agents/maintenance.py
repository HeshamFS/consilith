"""Maintenance the server runs by itself (BUILD_SPEC sections 6 and 9).

At startup: orphan runs are failed as "interrupted by restart", peer deliveries whose lease ran
out go back to the queue, and confirm cards past their 15 minutes are expired. A periodic task
then runs the retention purges: chat turns (30 days, and the last 500 per person per room), peer
message bodies (30 days), finished peer deliveries and the AG-UI chat events in the journal.

Every step is independent. One that fails is logged and the others still run, so a fault in a
purge can never stop the server from starting.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from ..domain.service import RoomService
from ..platform.store import Store
from .runs import reap_orphans

log = logging.getLogger("consilith.maintenance")

RETENTION_DAYS = 30.0
CHAT_TURNS_KEPT = 500
PURGE_EVERY_S = 3600.0


class Maintenance:
    def __init__(
        self,
        store: Store,
        rooms: Callable[[str], RoomService],
        *,
        clock: Callable[[], float] = time.time,
        interval_s: float = PURGE_EVERY_S,
    ) -> None:
        self.store = store
        self.rooms = rooms
        self._clock = clock
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    # --- startup --------------------------------------------------------------------------
    def startup(self) -> dict[str, int]:
        """Reap orphan runs, requeue expired peer deliveries, expire due confirm cards."""
        done: dict[str, int] = {}
        steps: dict[str, Callable[[], int]] = {
            "reaped_runs": lambda: reap_orphans(self.store, self.rooms),
            "requeued_deliveries": lambda: self.store.peer_deliveries.requeue_expired(
                self._clock()
            ),
            "expired_cards": lambda: self.store.pending_actions.expire_due(self._clock()),
        }
        for name, step in steps.items():
            try:
                done[name] = step()
            except Exception:
                log.exception("maintenance step %s failed", name)
                done[name] = -1
        return done

    # --- retention ------------------------------------------------------------------------
    def purge(self) -> dict[str, int]:
        """The retention purges. Returns how many rows each removed (-1: the step failed)."""
        now = self._clock()
        cutoff = now - RETENTION_DAYS * 86400.0
        done: dict[str, int] = {}
        steps: dict[str, Callable[[], int]] = {
            "chat_turns_old": lambda: self.store.chat_turns.purge_older_than(cutoff),
            "chat_turns_trimmed": self._trim_chat_turns,
            "peer_messages": lambda: self.store.peer_messages.purge_older_than_days(
                RETENTION_DAYS, now
            ),
            "peer_deliveries": lambda: self.store.peer_deliveries.purge_finished_older_than(cutoff),
            "journal_chat": lambda: self.store.journal_purge_before(cutoff, "AG-UI"),
        }
        for name, step in steps.items():
            try:
                done[name] = step()
            except Exception:
                log.exception("retention step %s failed", name)
                done[name] = -1
        return done

    def _trim_chat_turns(self) -> int:
        """Keep each person's last 500 turns in each room."""
        removed = 0
        for room_id, _title in self.store.rooms():
            people = (self.rooms(room_id).read("room", "people") or {}).keys()
            for person in list(people):
                removed += self.store.chat_turns.trim(room_id, person, CHAT_TURNS_KEPT)
        return removed

    # --- the periodic task ----------------------------------------------------------------
    def start_periodic(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._periodic())

    async def _periodic(self) -> None:
        while True:
            await asyncio.sleep(self._interval_s)
            await asyncio.to_thread(self.purge)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


__all__ = ["CHAT_TURNS_KEPT", "RETENTION_DAYS", "Maintenance"]
