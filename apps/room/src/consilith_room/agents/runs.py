"""Runs as background jobs (BUILD_SPEC section 6, "Runs: approval grant and detached jobs").

``run.start`` never awaits a workflow inside the chat request. ``RunSupervisor.start`` makes a
job with a deterministic id from the ledger key (``job-`` plus a hash of
``msg_id:step.index:args-digest``), writes a ``queue`` entry in the room document, and either
hands the job to the tier-2 queue (a declared queue mode, or a ``Denied`` that routes to tier 2)
or runs it as a tracked, request-independent ``asyncio`` task. A semaphore of two bounds the
inline tasks. A retry or a reconnect with the same ledger key finds the job and starts nothing.

Minimal quotas live here: two active jobs per person, three per room, dedupe on
``(case, mode, lane revisions)``, and cancel. A failure carries the real error text. At startup
``reap_orphans`` fails every run that is still active with no live job.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from consilith_contracts.extensions import optional_extension

from ..domain.actors import Actor
from ..domain.errors import Denied, NotFound, RoomError
from ..domain.service import RUN_ACTIVE, RoomService
from ..platform.store import Store
from .conductor import CONDUCTOR, current_job, error_text

log = logging.getLogger("consilith.runs")

JOB_PREFIX = "job-"
MAX_ACTIVE_PER_PERSON = 2
MAX_ACTIVE_PER_ROOM = 3
INLINE_SLOTS = 2
ACTIVE = ("queued", "running")
INTERRUPTED = "interrupted by restart"

Solve = Callable[[RoomService, Actor, str, str], Awaitable[Any]]
Authorize = Callable[[RoomService, Actor, str], None]


class QuotaExceeded(RoomError):
    """A person or room already has as many active jobs as it may."""

    status = 429
    code = "quota"


def job_id_for(ledger_key: str) -> str:
    """The deterministic job id of a ledger key: the same call is the same job on every retry."""
    return JOB_PREFIX + hashlib.sha256(ledger_key.encode()).hexdigest()[:16]


def active_jobs(room: RoomService) -> list[dict[str, Any]]:
    return [e for e in (room.read("queue") or {}).values() if e.get("status") in ACTIVE]


def _summary(out: Any) -> dict[str, Any]:
    return {
        "runs": getattr(out, "runs", []),
        "stamps": getattr(out, "stamps", {}),
        "error": getattr(out, "error", None),
    }


# An automatic turn: the room tells an agent that a run it started has settled, so it can carry
# on with what its person asked (Hesham, 2026-10-22: hand the result to another agent, write the
# report, ...). A run started in such a turn gets no follow-up of its own, so it never chains.
AUTO_PREFIX = "(Automatic) "


def follow_up_text(case: str, status: str, request: str, error: str | None = None) -> str:
    how = "finished" if status == "done" else f"ended as {status}"
    why = f" ({error})" if error and status != "done" else ""
    return (
        f"{AUTO_PREFIX}The run you started for {case} {how}{why}. Continue what your person "
        f"asked when you started it, using the results in the room; do not start the same run "
        f"again. Their request was: {request}"
    )


Settled = Callable[["RoomService", dict[str, Any]], Awaitable[None]]


class RunSupervisor:
    """Starts runs with deterministic job ids, enforces quotas, cancels and reaps."""

    # Called once a job with a ``followUp`` settles; the runtime runs the automatic turn.
    on_settled: Settled | None = None
    # Called for every job that settles: the runtime tells the agents in the requester's open
    # threads, so the other side knows without asking (no model call).
    on_status: Callable[[RoomService, dict[str, Any]], None] | None = None

    def __init__(
        self,
        store: Store,
        rooms: Callable[[str], RoomService] | None,
        solve: Solve,
        *,
        tier2: Any = None,
        authorize: Authorize | None = None,
        slots: int = INLINE_SLOTS,
    ) -> None:
        self.store = store
        self.rooms = rooms
        self._solve = solve
        self.tier2 = tier2
        self._authorize = authorize
        self._slots = asyncio.Semaphore(slots)
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        if tier2 is not None and hasattr(tier2, "on_settled"):
            tier2.on_settled = self._settled  # a durable job hands over like an inline one

    @classmethod
    def for_kernel(cls, kernel: Any) -> RunSupervisor:
        """The supervisor over a kernel's conductor, solve and tier-2 queue."""
        tier2 = getattr(kernel, "tier2", None)

        async def solve(room: RoomService, actor: Actor, case: str, mode: str) -> Any:
            return await kernel.solve(room, actor, case=case, mode=mode)

        conductor = kernel.conductor
        return cls(
            conductor.store,
            getattr(tier2, "rooms", None),
            solve,
            tier2=tier2,
            authorize=conductor.authorize,
        )

    # --- start ----------------------------------------------------------------------------
    async def start(
        self, room: RoomService, requester: Actor, case: str, mode: str, ledger_key: str
    ) -> str:
        """Start a job for ``(case, mode)``, or return the one a retry of the same call
        already started. Raises ``Denied`` or ``Locked`` now, and ``QuotaExceeded`` over a
        quota, so the person hears why before any job exists."""
        return (await self.begin(room, requester, case, mode, ledger_key))[0]

    async def begin(
        self, room: RoomService, requester: Actor, case: str, mode: str, ledger_key: str
    ) -> tuple[str, str]:
        """``start`` with what happened: ``(job id, how)``, where ``how`` is ``started``,
        ``retry`` (the same call was already started) or ``duplicate`` (an active job for the
        same case, mode and lane revisions was reused)."""
        jid = job_id_for(ledger_key)
        if room.read("queue", jid) is not None:
            return jid, "retry"
        if self._authorize is not None:
            self._authorize(room, requester, case)
        room.require_ready(case)
        revisions = room.revisions(case)
        person = requester.person_id
        active = active_jobs(room)
        for entry in active:  # dedupe: the same inputs are already being computed
            if (
                entry.get("case") == case
                and entry.get("mode") == mode
                and entry.get("revisions") == revisions
            ):
                return str(entry["id"]), "duplicate"
        if person is not None:
            mine = sum(1 for e in active if e.get("requestedByPerson") == person)
            if mine >= MAX_ACTIVE_PER_PERSON:
                raise QuotaExceeded(
                    f"you already have {mine} jobs running; wait for one to finish or cancel one"
                )
        if len(active) >= MAX_ACTIVE_PER_ROOM:
            raise QuotaExceeded(
                f"this room already has {len(active)} jobs running; try again when one finishes"
            )
        queue_modes = optional_extension("queue_modes", case) or []
        if mode in queue_modes and self.tier2 is not None:
            self.tier2.enqueue(
                room,
                case,
                requester,
                mode,
                "run requested in chat",
                revisions=revisions,
                job_id=jid,
            )
            return jid, "started"
        room.set_queue_entry(jid, self._entry(jid, "queued", case, mode, requester, revisions))
        task = asyncio.get_running_loop().create_task(
            self._run_inline(room, jid, requester, case, mode), name=jid
        )
        key = (room.id, jid)
        self._tasks[key] = task
        task.add_done_callback(lambda _t: self._tasks.pop(key, None))
        return jid, "started"

    @staticmethod
    def _entry(
        jid: str,
        status: str,
        case: str,
        mode: str,
        requester: Actor,
        revisions: dict[str, str],
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": jid,
            "kind": "inline",
            "status": status,
            "case": case,
            "mode": mode,
            "meshLevel": mode,
            "why": "run requested in chat",
            "requestedBy": requester.id,
            "requestedByPerson": requester.person_id,
            "revisions": revisions,
            "created": time.time(),
        }
        if requester.grant is not None:
            entry["approvedBy"] = requester.grant.approved_by
        return entry

    def _set(self, room: RoomService, jid: str, **fields: Any) -> None:
        entry = dict(room.read("queue", jid) or {})
        entry.update({k: v for k, v in fields.items() if v is not None})
        room.set_queue_entry(jid, entry)

    async def _run_inline(
        self, room: RoomService, jid: str, requester: Actor, case: str, mode: str
    ) -> None:
        """The job's own task. Whatever happens, the entry ends in a final state with the real
        reason; nothing here depends on the request that started it."""
        try:
            async with self._slots:
                self._set(room, jid, status="running")
                current_job.set(jid)  # this task has its own copy of the context
                out = await self._solve(room, requester, case, mode)
            error = getattr(out, "error", None)
            self._set(
                room,
                jid,
                status="failed" if error else "done",
                result=_summary(out),
                error=error,
            )
            self._settled(room, jid)
        except asyncio.CancelledError:
            self._set(room, jid, status="cancelled", error="cancelled")
            raise
        except Denied as exc:
            if exc.details.get("route") == "tier2" and self.tier2 is not None:
                moved = self.tier2.enqueue(
                    room,
                    case,
                    requester,
                    mode,
                    exc.message,
                    revisions=room.revisions(case),
                    job_id=f"{jid}-t2",
                )
                self._set(room, jid, status="moved", movedTo=moved, error=exc.message)
            else:
                self._set(room, jid, status="failed", error=error_text(exc))
        except Exception as exc:
            log.exception("job %s failed", jid)
            self._set(room, jid, status="failed", error=error_text(exc))

    def _settled(self, room: RoomService, jid: str) -> None:
        """Hand a settled job with a follow-up to the runtime, once, outside the job's slot."""
        entry = dict(room.read("queue", jid) or {})
        if self.on_status is not None:
            try:
                self.on_status(room, entry)
            except Exception:
                log.exception("telling the threads about job %s failed", jid)
        if self.on_settled is None or not entry.get("followUp") or entry.get("followedUp"):
            return
        self._set(room, jid, followedUp=True)
        callback = self.on_settled

        async def go() -> None:
            try:
                await callback(room, entry)
            except Exception:
                log.exception("the follow-up of job %s failed", jid)

        asyncio.get_running_loop().create_task(go(), name=f"{jid}-follow-up")

    # --- cancel ---------------------------------------------------------------------------
    async def cancel(self, room: RoomService, job_id: str, by: str) -> bool:
        """Cancel one of ``by``'s own jobs. Returns whether it was still active, so a repeat is
        a harmless no-op. Another person's job is ``Denied``."""
        entry = (room.read("queue") or {}).get(job_id)
        if entry is None:
            raise NotFound(f"no job {job_id}")
        if entry.get("requestedByPerson") != by:
            raise Denied("you can cancel only your own jobs")
        if entry.get("status") not in ACTIVE:
            return False
        if entry.get("kind") == "tier2":
            if self.tier2 is None or not self.tier2.cancel(room, job_id):
                self._set(room, job_id, status="cancelled", error="cancelled")
            return True
        task = self._tasks.get((room.id, job_id))
        if task is None:  # no live task: a leftover from before a restart
            self._fail_runs_of(room, job_id, "cancelled", "cancelled")
            self._set(room, job_id, status="cancelled", error="cancelled")
            return True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    @staticmethod
    def _fail_runs_of(room: RoomService, job_id: str, status: str, text: str) -> int:
        n = 0
        for rid, run in list((room.read("runs") or {}).items()):
            if run.get("jobId") == job_id and run.get("status") == RUN_ACTIVE:
                room.fail_run(CONDUCTOR, rid, status, text, [])
                n += 1
        return n

    # --- reap -----------------------------------------------------------------------------
    async def reap(self) -> int:
        """Fail every run that is still active with no live job in this process."""
        if self.rooms is None:
            return 0
        live = {jid for _room, jid in self._tasks}
        return reap_orphans(self.store, self.rooms, live)


def reap_orphans(
    store: Store,
    rooms: Callable[[str], RoomService],
    live: set[str] | frozenset[str] = frozenset(),
) -> int:
    """At startup: every run still ``running`` with no live job is failed as "interrupted by
    restart", and an inline job that was queued or running is failed the same way. A tier-2 job
    that was running is requeued by the queue itself; its entry goes back to ``queued`` and
    records ``supersedes`` with the run it replaces. Returns how many runs and jobs it ended."""
    ended = 0
    running_jobs = {(j["room"], j["id"]) for j in store.jobs("running")}
    for room_id, _title in store.rooms():
        try:
            room = rooms(room_id)
        except Exception:
            log.exception("reaper could not load room %s", room_id)
            continue
        for rid, run in list((room.read("runs") or {}).items()):
            jid = run.get("jobId")
            if run.get("status") != RUN_ACTIVE or jid in live:
                continue
            room.fail_run(CONDUCTOR, rid, "failed", INTERRUPTED, [])
            ended += 1
            entry = (room.read("queue") or {}).get(jid) if jid is not None else None
            if jid is not None and entry is not None and (room_id, jid) in running_jobs:
                room.set_queue_entry(jid, {**entry, "status": "queued", "supersedes": rid})
        for jid, entry in list((room.read("queue") or {}).items()):
            if entry.get("kind") == "inline" and entry.get("status") in ACTIVE and jid not in live:
                room.set_queue_entry(jid, {**entry, "status": "failed", "error": INTERRUPTED})
                ended += 1
    return ended


def supervisor_of(kernel: Any) -> RunSupervisor:
    """The one supervisor of a kernel, made on first use and kept on it as ``kernel.runs``."""
    found = getattr(kernel, "runs", None)
    if isinstance(found, RunSupervisor):
        return found
    if getattr(kernel, "conductor", None) is None:
        raise RoomError("runs are not available in this server", code="not_yet")
    made = RunSupervisor.for_kernel(kernel)
    kernel.runs = made
    return made


__all__ = [
    "INLINE_SLOTS",
    "MAX_ACTIVE_PER_PERSON",
    "MAX_ACTIVE_PER_ROOM",
    "QuotaExceeded",
    "RunSupervisor",
    "active_jobs",
    "job_id_for",
    "reap_orphans",
    "supervisor_of",
]
