"""Tier 2: the accurate queue (FR-LOOP-5, NFR-LAT-3).

Accurate runs, and quick runs over the requester's compute budget, are queued. Jobs persist in
SQLite and appear in room state under /queue. A worker runs them one at a time; when an
accurate result lands and its inputs still match, it supersedes the quick value and the referee
checks it. The ledger keeps both. A job that was running when the server stopped is re-queued.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from consilith_contracts import new_id

from ..domain.actors import Actor
from ..domain.service import RoomService
from ..platform.store import Store

log = logging.getLogger("consilith.tier2")
Solve = Callable[[RoomService, Actor, str, str], Awaitable[Any]]


class Tier2Queue:
    def __init__(
        self,
        store: Store,
        rooms: Callable[[str], RoomService],
        solve: Solve,
        on_state_change: Callable[[RoomService, str], None] | None = None,
    ) -> None:
        self.store = store
        self.rooms = rooms
        self.solve = solve
        self.on_state_change = on_state_change
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    def _set_entry(self, room: RoomService, jid: str, entry: dict[str, Any]) -> None:
        room.set_queue_entry(jid, entry)
        if self.on_state_change is not None:
            try:
                self.on_state_change(room, str(entry["case"]))
            except Exception:
                # A presentation fault cannot change a durable computation outcome.
                log.exception("could not publish queue state for %s", jid)

    def enqueue(
        self,
        room: RoomService,
        case: str,
        requester: Actor,
        mode: str,
        why: str,
        revisions: dict[str, str] | None = None,
    ) -> str:
        jid = new_id("job")
        spec = {
            "case": case,
            "requester": {"kind": requester.kind, "id": requester.id, "owner": requester.owner},
            "mode": mode,
            "meshLevel": mode,
            "why": why,
            "queued": time.time(),
        }
        if revisions is not None:
            spec["revisions"] = revisions
        self.store.put_job(jid, room.id, "queued", spec)
        entry: dict[str, Any] = {
            "id": jid,
            "status": "queued",
            "case": case,
            "why": why,
            "mode": mode,
            "meshLevel": mode,
        }
        if revisions is not None:
            entry["revisions"] = revisions
        self._set_entry(room, jid, entry)
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None
        if self._loop is None or caller_loop is self._loop:
            self._notify()
        else:
            self._loop.call_soon_threadsafe(self._notify)
        return jid

    def _notify(self) -> None:
        self._idle.clear()
        self._wake.set()

    async def wait_idle(self, timeout_s: float = 30.0) -> None:
        """Wait on the queue's owning loop, including callers on another thread/loop."""
        if self._loop is None:
            raise RuntimeError("queue has not started")
        if asyncio.get_running_loop() is self._loop:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout_s)
        else:
            pending = asyncio.run_coroutine_threadsafe(self._idle.wait(), self._loop)
            await asyncio.wait_for(asyncio.wrap_future(pending), timeout=timeout_s)

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for job in self.store.jobs("running"):  # interrupted by a restart: run it again
            self.store.update_job(job["id"], "queued")
        if self.store.jobs("queued"):
            self._idle.clear()
            self._wake.set()
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            jobs = self.store.jobs("queued")
            if not jobs:
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            job = jobs[0]
            await self._one(job)

    async def _one(self, job: dict[str, Any]) -> None:
        jid, spec = job["id"], job["spec"]
        room = self.rooms(job["room"])
        mode = spec.get("mode", spec.get("meshLevel", "default"))
        req = spec["requester"]
        requester = (
            Actor.agent_of(req["owner"])
            if req["kind"] == "personal_agent"
            else Actor.person(req["id"])
        )
        self.store.update_job(jid, "running")
        self._set_entry(
            room,
            jid,
            {
                "id": jid,
                "status": "running",
                "case": spec["case"],
                "why": spec["why"],
                "mode": mode,
                "meshLevel": mode,
                "revisions": spec.get("revisions", {}),
            },
        )
        try:
            out = await self.solve(room, requester, spec["case"], mode)
            error = getattr(out, "error", None)
            status = "failed" if error else "done"
            summary = {
                "runs": getattr(out, "runs", []),
                "stamps": getattr(out, "stamps", {}),
                "error": error,
            }
            self.store.update_job(jid, status, result=summary, error=error)
            self._set_entry(
                room,
                jid,
                {
                    "id": jid,
                    "status": status,
                    "case": spec["case"],
                    "why": spec["why"],
                    "mode": mode,
                    "meshLevel": mode,
                    "revisions": spec.get("revisions", {}),
                    "result": summary,
                },
            )
        except Exception as exc:
            log.exception("tier-2 job %s failed", jid)
            self.store.update_job(jid, "failed", error=type(exc).__name__)
            self._set_entry(
                room,
                jid,
                {
                    "id": jid,
                    "status": "failed",
                    "case": spec["case"],
                    "why": spec["why"],
                    "mode": mode,
                    "meshLevel": mode,
                    "revisions": spec.get("revisions", {}),
                    "error": type(exc).__name__,
                },
            )
