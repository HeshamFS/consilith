"""Tier 2: the accurate queue (FR-LOOP-5, NFR-LAT-3).

Accurate runs, and quick runs over the requester's compute budget, are queued. Jobs persist in
SQLite and appear in room state under /queue. A worker runs them one at a time; when an
accurate result lands and its inputs still match, it supersedes the quick value and the referee
checks it. The ledger keeps both. A job that was running when the server stopped is re-queued;
the run it left active is failed and the re-run records ``supersedes`` with that run's id.
Rooms take turns, so one busy room cannot hold the single worker while others wait.

A job that a person approved on a confirm card keeps that approval in its spec (the card's
digest, the approver, the case and the mode). The worker checks the digest again against the lane
revisions and plugin pins as they are when it takes the job, and refuses the job if they moved.
It runs the job with a fresh grant only after that check.

Owners: the startup sweep in ``Maintenance`` (``reap_orphans``) is the one place that fails
orphaned runs; ``reap`` here only re-queues the jobs and fails a run itself only if that sweep
did not (it checks the run is still active, so a run is never failed twice). Every job runs as
its own task (a "flight"); ``cancel`` (routes, ``RunSupervisor``) and ``interrupt`` both cancel
that flight, and the worker records the job as cancelled once.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from consilith_contracts import new_id

from ..domain.actors import Actor, Grant
from ..domain.errors import Conflict, NotFound
from ..domain.service import GRANT_TTL_S, RUN_ACTIVE, RoomService
from ..platform.store import Store
from ..protocols.mcp_client import progress_sink
from .conductor import (
    CONDUCTOR,
    current_job,
    declared_mode,
    error_text,
    job_run_sink,
    run_deadline_s,
    run_meta_for,
)

log = logging.getLogger("consilith.tier2")
Solve = Callable[[RoomService, Actor, str, str], Awaitable[Any]]
PROGRESS_WRITE_INTERVAL_S = 1.0  # a room commit per tick would flood the feed


def pick_fair(jobs: list[dict[str, Any]], served: dict[str, int]) -> dict[str, Any]:
    """The next job: the oldest queued job of the room served least recently.

    ``served`` maps a room to the tick it was last served; a room never served counts as 0, so a
    new room goes ahead of one that just ran. ``jobs`` is in creation order, so within a room the
    oldest goes first, and a tie between rooms goes to the room whose job came first."""
    best = jobs[0]
    for job in jobs:
        if served.get(job["room"], 0) < served.get(best["room"], 0):
            best = job
    return best


CHANGED_AFTER_APPROVAL = "inputs changed after you approved; ask again"
ACTIVE = ("queued", "running")


def grant_spec(grant: Grant) -> dict[str, Any]:
    """The part of a run grant a queued job stores. Never model text: it came from a card."""
    return {
        "kind": grant.kind,
        "digest": grant.digest,
        "approvedBy": grant.approved_by,
        "case": grant.case,
        "mode": grant.mode,
        "revisions": grant.revisions_map(),
    }


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
        self._served: dict[str, int] = {}
        self._tick = 0
        self._live: dict[str, dict[str, Any]] = {}
        self._flights: dict[str, asyncio.Task[None]] = {}
        self._stopping = False
        self._last_progress_write = 0.0
        self.maintenance: Any = None
        # The run supervisor's hand-over: a settled job's follow-up turn and room update.
        self.on_settled: Callable[[RoomService, str], None] | None = None

    def _set_entry(self, room: RoomService, jid: str, entry: dict[str, Any]) -> None:
        """Every queue entry write: the entry gains the job's live fields, is committed, and the
        state-change hook fires."""
        entry = self.decorate(jid, entry)
        # The entry is rebuilt from the job's spec on every write; the hand-over fields the
        # supervisor added (who asked, and whether it was handed back) are kept.
        before = room.read("queue", jid) or {}
        for key in ("followUp", "followedUp"):
            if key in before and key not in entry:
                entry[key] = before[key]
        room.set_queue_entry(jid, entry)
        if self.on_state_change is not None:
            try:
                self.on_state_change(room, str(entry.get("case", "")))
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
        *,
        job_id: str | None = None,
        grant: Grant | None = None,
    ) -> str:
        """Queue a job. ``job_id`` makes it idempotent: the same id returns the job already
        queued. A grant (explicit, or the requester's own when it was approved for this mode)
        is stored in the spec for the worker to check again."""
        if job_id is not None and room.read("queue", job_id) is not None:
            return job_id
        jid = job_id or new_id("job")
        if grant is None and requester.grant is not None and requester.grant.mode == mode:
            grant = requester.grant
        spec: dict[str, Any] = {
            "case": case,
            "requester": {"kind": requester.kind, "id": requester.id, "owner": requester.owner},
            "mode": mode,
            "meshLevel": mode,
            "why": why,
            "queued": time.time(),
            "requestedByPerson": requester.person_id,
        }
        if revisions is not None:
            spec["revisions"] = revisions
        if grant is not None:
            spec["grant"] = grant_spec(grant)
        self.store.put_job(jid, room.id, "queued", spec)
        self._set_entry(room, jid, self._entry(jid, "queued", spec))
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            caller_loop = None
        if self._loop is None or caller_loop is self._loop:
            self._notify()
        else:
            self._loop.call_soon_threadsafe(self._notify)
        return jid

    @staticmethod
    def _entry(jid: str, status: str, spec: dict[str, Any], **extra: Any) -> dict[str, Any]:
        mode = spec.get("mode", spec.get("meshLevel", "default"))
        entry: dict[str, Any] = {
            "id": jid,
            "kind": "tier2",
            "status": status,
            "case": spec["case"],
            "why": spec["why"],
            "mode": mode,
            "meshLevel": mode,
            "requestedBy": spec["requester"]["id"],
            "requestedByPerson": spec.get("requestedByPerson"),
            "revisions": spec.get("revisions", {}),
        }
        grant = spec.get("grant")
        if grant:
            entry["approvedBy"] = grant["approvedBy"]
        entry.update({k: v for k, v in extra.items() if v is not None})
        return entry

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
        self._start_maintenance()
        self.reap()
        if self.store.jobs("queued"):
            self._idle.clear()
            self._wake.set()
        self._task = asyncio.get_running_loop().create_task(self._run())

    def _start_maintenance(self) -> None:
        """The startup sweep (orphan runs, expired deliveries and cards) runs before the queue
        takes its first job; the retention purges then repeat in the background. A fault here
        is logged and never stops the server."""
        from .maintenance import Maintenance

        try:
            self.maintenance = Maintenance(self.store, self.rooms)
            self.maintenance.startup()
            self.maintenance.start_periodic()
        except Exception:
            log.exception("startup maintenance failed")

    def reap(self) -> None:
        """Jobs left ``running`` by a restart: queue the same job again. The entry keeps its id (no
        second record) and names the run it supersedes. The startup sweep has normally failed
        that run already; if it did not, it is failed here (only while still active)."""
        for job in self.store.jobs("running"):
            jid = job["id"]
            try:
                room = self.rooms(job["room"])
                entry = dict(room.read("queue", jid) or {})
                old = entry.get("supersedes") or entry.get("runId")
                if entry.get("runId"):
                    self._fail_active_run(room, str(entry["runId"]), "interrupted by restart")
                entry.pop("progress", None)
                entry.update({"status": "queued", **({"supersedes": old} if old else {})})
                self._set_entry(room, jid, entry)
            except Exception:
                log.exception("could not reap tier-2 job %s", jid)
            self.store.update_job(jid, "queued")

    @staticmethod
    def _fail_active_run(room: RoomService, rid: str, reason: str) -> None:
        try:
            if room.read("runs", rid, "status") == RUN_ACTIVE:
                room.fail_run(CONDUCTOR, rid, "failed", reason, [])
        except (Conflict, NotFound):
            pass

    def decorate(self, jid: str, entry: dict[str, Any]) -> dict[str, Any]:
        """``entry`` with the job's live fields. A finished job shows a full bar."""
        out = {**self._live.get(jid, {}), **entry}
        if out.get("status") == "done":
            out["progress"] = {"fraction": 1.0, "updatedAt": time.time()}
        return out

    def _begin(self, room: RoomService, jid: str, spec: dict[str, Any], mode: str) -> None:
        prior = room.read("queue", jid) or {}
        meta = run_meta_for(room, spec["case"], mode)
        live: dict[str, Any] = {"startedAt": time.time()}
        if prior.get("supersedes"):
            live["supersedes"] = prior["supersedes"]
        if meta is not None and meta.estimate_s:
            live["estimateS"] = meta.estimate_s
            live["deadlineS"] = run_deadline_s(2, meta)
        self._live[jid] = live

    def _on_progress(
        self, room: RoomService, jid: str, progress: float, total: float | None, message: str | None
    ) -> None:
        """Record a tick on the queue entry, at most once per interval; the last always lands.
        It is written like every other entry write, through ``_set_entry``."""
        final = bool(total) and progress >= (total or 0)
        now = time.monotonic()
        if not final and now - self._last_progress_write < PROGRESS_WRITE_INTERVAL_S:
            return
        self._last_progress_write = now
        fraction = min(1.0, max(0.0, progress / total)) if total and total > 0 else None
        snap: dict[str, Any] = {"fraction": fraction, "updatedAt": time.time()}
        if message:
            snap["message"] = message
        self._live.setdefault(jid, {})["progress"] = snap
        entry = dict(room.read("queue", jid) or {})
        if entry.get("status") == "running":
            self._set_entry(room, jid, {**entry, "progress": snap})

    def interrupt(self, jid: str) -> bool:
        """Cancel the job now running, if it is ``jid``. The worker records it as cancelled."""
        task = self._flights.get(jid)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def stop(self) -> None:
        self._stopping = True
        if self.maintenance is not None:
            await self.maintenance.stop()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def cancel(self, room: RoomService, jid: str) -> bool:
        """Cancel a queued or running job. Returns whether it was still active. A running job's
        flight is cancelled (as ``interrupt`` does); the conductor then fails its open run as
        cancelled and the worker records the job as cancelled."""
        job = next((j for j in self.store.jobs() if j["id"] == jid and j["room"] == room.id), None)
        if job is None or job["status"] not in ACTIVE:
            return False
        self.store.update_job(jid, "cancelled", error="cancelled")
        self._set_entry(room, jid, self._entry(jid, "cancelled", job["spec"], error="cancelled"))
        self.interrupt(jid)
        return True

    async def _run(self) -> None:
        while True:
            jobs = self.store.jobs("queued")
            if not jobs:
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            job = pick_fair(jobs, self._served)
            self._tick += 1
            self._served[job["room"]] = self._tick
            flight = asyncio.create_task(self._guarded(job))  # its own context for the sinks
            self._flights[job["id"]] = flight
            try:
                await flight
            except asyncio.CancelledError:
                me = asyncio.current_task()
                if self._stopping or (me is not None and me.cancelling()):
                    raise
            finally:
                self._flights.pop(job["id"], None)
                self._live.pop(job["id"], None)

    async def _guarded(self, job: dict[str, Any]) -> None:
        try:
            await self._one(job)
        except asyncio.CancelledError:
            if not self._stopping:  # a shutdown leaves the job running, so a restart re-queues it
                self._record_cancelled(job)
            raise

    def _record_cancelled(self, job: dict[str, Any]) -> None:
        """A cancelled flight: fail the run it left open and show the job as cancelled (once;
        ``cancel`` may have written the entry already)."""
        jid, spec = job["id"], job["spec"]
        room = self.rooms(job["room"])
        entry = dict(room.read("queue", jid) or {})
        run = self._live.get(jid, {}).get("runId") or entry.get("runId")
        if run:
            self._fail_active_run(room, str(run), "cancelled")
        if entry.get("status") == "cancelled":
            return
        self.store.update_job(jid, "cancelled", error="cancelled")
        entry.update({"id": jid, "status": "cancelled", "case": spec["case"], "why": spec["why"]})
        self._set_entry(room, jid, entry)

    def _requester(self, room: RoomService, spec: dict[str, Any]) -> Actor | str:
        """The actor the job runs as, or the reason it must not run. A stored grant is checked
        against the lane revisions and plugin pins as they are now."""
        req = spec["requester"]
        requester = (
            Actor.agent_of(req["owner"])
            if req["kind"] == "personal_agent"
            else Actor.person(req["id"])
        )
        stored = spec.get("grant")
        if not stored:
            return requester
        digest = room.run_card_digest(stored["approvedBy"], stored["case"], stored["mode"])
        if digest != stored["digest"]:
            return CHANGED_AFTER_APPROVAL
        grant = Grant.of(
            stored["kind"],
            stored["digest"],
            stored["approvedBy"],
            stored.get("revisions", {}),
            time.time() + GRANT_TTL_S,
            case=stored["case"],
            mode=stored["mode"],
        )
        return requester.with_grant(grant)

    async def _one(self, job: dict[str, Any]) -> None:
        jid, spec = job["id"], job["spec"]
        room = self.rooms(job["room"])
        mode = spec.get("mode", spec.get("meshLevel", "default"))
        prior = (room.read("queue", jid) or {}).get("supersedes")
        requester = self._requester(room, spec)
        if isinstance(requester, str):  # refused: the inputs moved since the person approved
            self.store.update_job(jid, "failed", error=requester)
            self._set_entry(room, jid, self._entry(jid, "failed", spec, error=requester))
            return
        self._begin(room, jid, spec, mode)
        # this flight has its own copy of the context, so these sinks are the job's alone
        current_job.set(jid)
        declared_mode.set(mode)
        job_run_sink.set(lambda rid: self._live.setdefault(jid, {}).update(runId=rid))
        progress_sink.set(lambda p, t, m: self._on_progress(room, jid, p, t, m))
        self.store.update_job(jid, "running")
        self._set_entry(room, jid, self._entry(jid, "running", spec, supersedes=prior))
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
                room, jid, self._entry(jid, status, spec, result=summary, supersedes=prior)
            )
            self._settle(room, jid)
        except asyncio.CancelledError:
            raise  # the flight records the cancel (or a shutdown leaves the job running)
        except Exception as exc:
            text = error_text(exc)
            log.exception("tier-2 job %s failed", jid)
            self.store.update_job(jid, "failed", error=text)
            self._set_entry(
                room, jid, self._entry(jid, "failed", spec, error=text, supersedes=prior)
            )
            self._settle(room, jid)

    def _settle(self, room: RoomService, jid: str) -> None:
        if self.on_settled is None:
            return
        try:
            self.on_settled(room, jid)
        except Exception:
            # A hand-over fault cannot change a durable computation outcome.
            log.exception("the hand-over of job %s failed", jid)
