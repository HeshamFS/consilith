"""The room server's HTTP surface. Thin: every rule lives in the domain."""

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from ag_ui.core import RunAgentInput
from consilith_contracts import AgentConfig, MemoryScope, PatchOp, new_id
from consilith_contracts.extensions import (
    all_extensions,
    describe_case,
    optional_extension,
    registry_scope,
)
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field

from ..agents.session import agent_session
from ..domain.actors import Actor
from ..domain.errors import Denied, NotFound, RoomError
from ..domain.service import RoomService
from ..platform.settings import Settings, get_settings
from ..protocols import illustrative
from ..protocols.a2a_host import A2ADispatch
from ..protocols.agui import Emitter, feed_stream
from .admission import Admission, admission_router
from .auth import Session, auth_router, make_auth
from .dev import dev_router, write_capability
from .hub import Hub
from .me import me_agent_router
from .plugins import plugins_router
from .schemas import Body

logger = logging.getLogger("consilith.api")
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
ARTIFACT_TYPES = {
    "image/png",
    "image/jpeg",
    "text/plain",
    "application/json",
    "application/octet-stream",
}
MAX_ARTIFACT = 10 * 1024 * 1024


class TwinIn(Body):
    patch: list[PatchOp] = Field(min_length=1)
    reason: str = Field(min_length=1)
    op_id: str | None = Field(default=None, alias="opId")


class DecisionIn(Body):
    accept: bool
    reason: str = Field(min_length=1)
    op_id: str | None = Field(default=None, alias="opId")


class LessonIn(Body):
    scope: MemoryScope
    text: str = Field(min_length=1)
    case: str | None = None
    lane: str | None = None


class LessonEditIn(Body):
    text: str | None = None
    delete: bool = False


class SolveIn(Body):
    mode: str | None = Field(default=None, min_length=1, max_length=100)
    # Legacy wire alias; supported modes and their meaning belong to the installed package.
    mesh_level: str | None = Field(default=None, alias="meshLevel", max_length=100)
    as_agent: bool = Field(default=False, alias="asAgent")  # requested through the person's agent


class ReplayIn(Body):
    session: Literal["feed", "agent"] = "feed"
    viewer: str | None = None
    run_id: str | None = Field(default=None, alias="runId")


class AnalysisIn(Body):
    case: str
    source: str = Field(min_length=1, max_length=4000)
    question: str = Field(default="", max_length=500)


class ComposeIn(Body):
    preview: bool = False
    request: str = Field(default="", max_length=2000)


class IntakeIn(Body):
    kind: str | None = Field(default=None, max_length=40)
    text: str = Field(default="", max_length=2000)


class BringIn(Body):
    card_url: str = Field(alias="cardUrl", max_length=2048)


class ExploreIn(Body):
    overrides: list[str] = Field(default_factory=list[str])


class PresenceIn(Body):
    cursor: list[float] | None = None
    viewport: dict[str, float] | None = None
    focus: str | None = None
    case: str | None = None


def create_app(settings: Settings | None = None, hub: Hub | None = None) -> FastAPI:
    settings = settings or get_settings()
    hub = hub or Hub(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        hub.bind_loop(asyncio.get_running_loop())
        hub.runtime.tier2.start()
        try:
            yield
        finally:
            await hub.runtime.tier2.stop()

    app = FastAPI(title="Consilith room server", version="0.1.0", lifespan=lifespan)
    app.state.hub = hub
    app.add_middleware(A2ADispatch, resolve=hub.runtime.hosted, tokens=hub.tokens)
    hub.asgi_app = app

    @app.exception_handler(RoomError)
    async def room_error(_req: Request, exc: RoomError) -> JSONResponse:
        body: dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details:
            body["details"] = exc.details
        return JSONResponse({"error": body}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_req: Request, exc: RequestValidationError) -> JSONResponse:
        """One error shape everywhere. The offending input is never echoed back: a body can hold
        a key, and a validation message must not repeat it."""
        fields = [{"loc": [str(x) for x in e["loc"]], "msg": str(e["msg"])} for e in exc.errors()]
        message = "the request is not valid: " + "; ".join(
            f"{'.'.join(f['loc'][1:]) or f['loc'][0]}: {f['msg']}" for f in fields[:5]
        )
        err = {"code": "invalid_request", "message": message, "details": {"fields": fields}}
        return JSONResponse({"error": err}, status_code=422)

    current, person = make_auth(hub)
    app.include_router(auth_router(hub, settings, current))
    app.include_router(plugins_router(hub, person))
    app.include_router(me_agent_router(hub, current))
    admission = Admission(hub)
    app.include_router(admission_router(admission, person))
    if settings.dev_mailbox and settings.env != "production":  # fail closed: not even routed
        app.include_router(dev_router(hub))

    def check_thread(inp: RunAgentInput, room_id: str, pid: str, session: str) -> None:
        """A thread id names its room, viewer and session; nobody uses another's thread."""
        if inp.thread_id != f"{room_id}:{pid}:{session}":
            raise Denied(f"threadId must be {room_id}:{pid}:{session}")

    def ensure_surface(room: RoomService, pid: str) -> None:
        """A feed always starts with the viewer's current surface of the room's studio. Someone
        who has none yet (a new seat, a quiet room) gets one composed at rest from the state."""
        case = str(room.read("room", "activeCase") or "")
        sid = f"{case}:{pid}:main"
        has = any(
            e["protocol"] == "A2UI"
            and e.get("viewer") == pid
            and e.get("refs", {}).get("surfaceId") == sid
            and "viewAs" not in e.get("refs", {})
            for e in hub.journal.entries(room.id)
        )
        if not has:
            try:
                hub.runtime.composer.surface(room, case, pid, "rest")
            except Exception:
                logger.exception("could not compose %s for %s", sid, pid)

    def seated(room_id: str, pid: str) -> RoomService:
        hub.refresh_plugins()
        room = hub.room(room_id)
        if room.read("room", "people", pid) is None:
            raise Denied(f"{pid} has no seat in room {room_id}")
        return room

    # identity ---------------------------------------------------------------------------
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        hub.refresh_plugins()
        with registry_scope():
            return {
                "contractVersion": "1",
                "capabilities": [describe_case(c) for c in all_extensions("cases").values()],
            }

    @app.get("/illustrative")
    def illustrative_set(_p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        """The server-owned set of illustrative tier-0 values (never solver results)."""
        return illustrative.wire()

    @app.get("/rooms/{room_id}")
    def room_state(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        room = seated(room_id, p)
        doc, seq = room.snapshot_at()
        return {"seq": seq, "state": doc}

    @app.post("/rooms/{room_id}/lanes/{case}/{lane}/claim")
    def claim(
        room_id: str, case: str, lane: str, p: Annotated[str, Depends(person)]
    ) -> dict[str, int]:
        return {"seq": seated(room_id, p).claim_lane(p, case, lane).seq}

    @app.post("/rooms/{room_id}/lanes/{case}/{lane}/release")
    def release(
        room_id: str, case: str, lane: str, p: Annotated[str, Depends(person)]
    ) -> dict[str, int]:
        return {"seq": seated(room_id, p).release_lane(p, case, lane).seq}

    @app.post("/rooms/{room_id}/twin")
    def edit_twin(room_id: str, body: TwinIn, p: Annotated[str, Depends(person)]) -> dict[str, int]:
        row = seated(room_id, p).edit_twin(Actor.person(p), body.patch, body.reason, body.op_id)
        return {"seq": row.seq}

    # the agent drawer ----------------------------------------------------------------------
    @app.get("/rooms/{room_id}/agents/{aid}/config")
    def get_config(room_id: str, aid: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        return seated(room_id, p).agent_config(aid).wire()

    @app.put("/rooms/{room_id}/agents/{aid}/config")
    def put_config(
        room_id: str, aid: str, body: AgentConfig, p: Annotated[str, Depends(person)]
    ) -> dict[str, int]:
        return {"seq": seated(room_id, p).set_agent_config(p, aid, body).seq}

    @app.put("/rooms/{room_id}/agents/{aid}/brought")
    async def bring(
        room_id: str, aid: str, body: BringIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        """Bring your own agent: an A2A Agent Card URL replaces the spawned agent."""
        room = seated(room_id, p)
        if aid != f"{p}.agent":
            raise Denied("only an agent's owner brings a replacement for it")
        return await hub.runtime.brought.bring(room, p, body.card_url)

    # proposals, decisions, warnings -------------------------------------------------------
    @app.post("/rooms/{room_id}/proposals/{prop_id}/decision")
    async def decide(
        room_id: str, prop_id: str, body: DecisionIn, p: Annotated[str, Depends(person)]
    ) -> Any:
        room = seated(room_id, p)
        dec, fresh = room.decide_once(p, prop_id, body.accept, body.reason, op_id=body.op_id)
        pending = room.pending_followup(prop_id)
        if pending is not None and hub.after_decision is not None:
            try:
                await hub.after_decision(room, room.proposal(prop_id), dec.accept)
            except Exception:
                ref = new_id("err")
                logger.exception("follow-up after decision %s failed [%s]", dec.id, ref)
                message = f"the decision stands; its follow-up failed (error {ref}); retry it"
                err = {"code": "followup_failed", "message": message}
                return JSONResponse({"error": err}, status_code=502)
            room.complete_followup(pending)  # only after success; a retry re-runs it
        return {**dec.wire(), "fresh": fresh}

    @app.post("/rooms/{room_id}/warnings/{wid}/ack")
    def ack(room_id: str, wid: str, p: Annotated[str, Depends(person)]) -> dict[str, bool]:
        seated(room_id, p).acknowledge(p, wid)
        return {"ok": True}

    # memory ---------------------------------------------------------------------------------
    @app.get("/rooms/{room_id}/memory")
    def memory(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        room = seated(room_id, p)
        return {
            "personal": [les.wire() for les in room.personal_lessons(p)],
            "room": room.read("memory", "room"),
            "case": room.read("memory", "case"),
        }

    @app.post("/rooms/{room_id}/memory", status_code=201)
    def add_lesson(
        room_id: str, body: LessonIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        les = seated(room_id, p).add_shared_lesson(p, body.scope, body.text, body.case, body.lane)
        return les.wire()

    @app.patch("/rooms/{room_id}/memory/personal/{lesson_id}")
    def edit_lesson(
        room_id: str, lesson_id: str, body: LessonEditIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, bool]:
        seated(room_id, p).edit_personal_lesson(p, lesson_id, delete=body.delete, text=body.text)
        return {"ok": True}

    # log and journal ------------------------------------------------------------------------
    @app.post("/rooms/{room_id}/artifacts", status_code=201)
    async def upload(
        room_id: str, request: Request, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        """Content-addressed inputs accepted by the platform or a room-pinned plugin."""
        room = seated(room_id, p)
        media = request.headers.get("content-type", "").split(";")[0].strip()
        extra_media = {
            media
            for plugin in room.read("room", "plugins") or []
            for media in plugin.get("artifactMediaTypes", [])
        }
        if media not in ARTIFACT_TYPES and media not in extra_media:
            raise RoomError(f"unsupported artifact type {media!r}")
        body = await request.body()
        if not body or len(body) > MAX_ARTIFACT:
            raise RoomError("an artifact is between 1 byte and 10 MB")
        sha = hashlib.sha256(body).hexdigest()
        hub.store.put_artifact(sha, media, body)
        room.note(Actor.person(p), "artifact.uploaded", {"sha256": sha, "mediaType": media})
        return {"sha256": sha, "mediaType": media, "bytes": len(body)}

    @app.get("/rooms/{room_id}/artifacts/{sha256}")
    def artifact(room_id: str, sha256: str, p: Annotated[str, Depends(person)]) -> Response:
        room = seated(room_id, p)

        from ..domain.artifacts import owns_artifact

        associated = owns_artifact(room, hub.store, sha256)
        stored = hub.store.artifact(sha256) if associated else None
        if stored is None:
            raise NotFound("artifact not found in this room")
        media, content = stored
        return Response(
            content,
            media_type=media,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "attachment",
                "Cache-Control": "private, immutable",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    @app.get("/rooms/{room_id}/queue")
    def queue(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        return {"jobs": seated(room_id, p).read("queue") or {}}

    @app.get("/rooms/{room_id}/log")
    def log(
        room_id: str, p: Annotated[str, Depends(person)], after: int = 0
    ) -> list[dict[str, Any]]:
        seated(room_id, p)
        return [r.__dict__ for r in hub.store.log(room_id, after)]

    @app.get("/rooms/{room_id}/journal")
    def journal(
        room_id: str, p: Annotated[str, Depends(person)], after: int = 0
    ) -> list[dict[str, Any]]:
        seated(room_id, p)
        # Viewer-addressed entries (a person's agent session, their surfaces) are private.
        return [e for e in hub.journal.entries(room_id, after) if e.get("viewer") in (None, p)]

    # presence (ephemeral, FR-MP-9) -------------------------------------------------------------
    @app.post("/rooms/{room_id}/presence")
    def presence(
        room_id: str, body: PresenceIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, bool]:
        seated(room_id, p)
        ev = {"person": p, "ts": time.time(), **body.model_dump(exclude_none=True)}
        hub.presence[room_id][p] = ev
        hub.bus.publish_ephemeral(room_id, ev)
        return {"ok": True}

    # solves ------------------------------------------------------------------------------
    @app.post("/solvers/discover")
    async def discover(_p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        tools = await hub.runtime.registry.discover()
        return {"tools": sorted(tools), "unreachable": hub.runtime.registry.unreachable}

    @app.post("/solvers/warm")
    async def warm_solvers(_p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        """One small call per solver before recording, so no cold start exceeds 2 s."""
        from ..agents.warmup import warm

        return await warm(hub.runtime.registry)

    @app.get("/rooms/{room_id}/surfaces/{case}")
    async def view_as(
        room_id: str,
        case: str,
        p: Annotated[str, Depends(person)],
        as_: Annotated[str | None, Query(alias="as")] = None,
    ) -> dict[str, Any]:
        """View as: the surface another person sees, recomposed from the same state (FR-UI-7)."""
        room = seated(room_id, p)
        viewer = as_ or p
        if room.read("room", "people", viewer) is None:
            raise NotFound(f"{viewer} has no seat")
        if case not in room.case_definitions():
            raise NotFound(f"no studio for {case}")
        if as_ is None:
            await hub.runtime.composer.prepare(
                room, case, viewer, hub.runtime.kernel.model(room, f"{viewer}.agent")
            )
        # Journaled like every A2UI message, to the person who asked (NFR-9, FR-PR-8).
        _seq, messages = hub.runtime.composer.surface(room, case, viewer, "rest", to=p)
        return {"viewer": viewer, "messages": messages}

    @app.post("/rooms/{room_id}/surfaces/{case}/compose")
    async def compose_workspace(
        room_id: str, case: str, body: ComposeIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        room = seated(room_id, p)
        if case not in room.case_definitions():
            raise NotFound(f"no studio for {case}")
        await hub.runtime.composer.prepare(
            room,
            case,
            p,
            hub.runtime.kernel.model(room, f"{p}.agent"),
            request=body.request or "Assemble the workspace for this task",
            preview=body.preview,
        )
        _seq, messages = hub.runtime.composer.surface(room, case, p, "rest")
        return {"viewer": p, "messages": messages}

    @app.post("/rooms/{room_id}/solve/{case}")
    async def solve_case(
        room_id: str, case: str, p: Annotated[str, Depends(person)], body: SolveIn | None = None
    ) -> dict[str, Any]:
        room = seated(room_id, p)
        if case not in room.case_definitions():
            raise NotFound(f"no studio for {case}")
        if not hub.runtime.registry.tools:
            await hub.runtime.registry.discover()
        requester = Actor.agent_of(p) if body and body.as_agent else Actor.person(p)
        res = await hub.runtime.kernel.request_solve(
            room, requester, case=case, mode=(body.mode or body.mesh_level) if body else None
        )
        if "queued" in res:
            return {"queued": res["queued"], "reason": res.get("reason", "accurate run")}
        out = res["outcome"]
        return {
            "runs": out.runs,
            "results": {q: r.wire() for q, r in out.results.items()},
            "stamps": out.stamps,
            "ledger": out.ledger,
            "error": out.error,
            "wallS": out.wall_s,
        }

    @app.post("/rooms/{room_id}/analysis")
    async def run_analysis(
        room_id: str, body: AnalysisIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        """A small analysis over existing results, checked by the referee before it is shown."""
        from ..agents.analysis import analyse

        room = seated(room_id, p)
        return await analyse(
            hub.runtime.conductor, room, Actor.person(p), body.case, body.source, body.question
        )

    @app.post("/rooms/{room_id}/intake")
    def intake(room_id: str, body: IntakeIn, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        """The conductor routes an intake by the studios' grammars (FR-LOOP-11)."""
        from ..agents.conductor import CONDUCTOR
        from ..agents.intake import route

        room = seated(room_id, p)
        out = route(body.kind, body.text, room.case_definitions())
        meta = {k: out.get(k) for k in ("supported", "case", "closest", "cannot")}
        room.note(CONDUCTOR, "intake.routed", {"kind": body.kind, "by": p, **meta})
        return out

    @app.post("/rooms/{room_id}/results/{result_id}/reproduce")
    async def reproduce_result(
        room_id: str, result_id: str, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        """Run a stored result again from its manifest alone and compare (FR-RG-7)."""
        from ..agents.reproduce import reproduce

        room = seated(room_id, p)
        if not hub.runtime.registry.tools:
            await hub.runtime.registry.discover()
        return await reproduce(
            hub.runtime.conductor,
            room,
            result_id,
            solver_apps=hub.runtime.settings.solver_apps,
            journal=hub.journal,
        )

    @app.post("/rooms/{room_id}/explore")
    async def explore(
        room_id: str, body: ExploreIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        """A person's own instruction to the swarm; it may override a lesson (FR-MEM-4)."""
        room = seated(room_id, p)
        if not hub.runtime.registry.tools:
            await hub.runtime.registry.discover()
        case = str(room.read("room", "activeCase") or "")
        results = room.current_results(case)
        primary = optional_extension("explore_quantities", case)
        rec = results.get(primary) if primary else next(iter(results.values()), None)
        if rec is None:
            raise RoomError("solve first: there is no result to explore from")
        if body.overrides:
            room.note(Actor.person(p), "memory.override", {"overrides": body.overrides, "by": p})
        notes = await hub.runtime.kernel.resolve_tension(
            room, f"{p}.agent", p, rec, results, overrides=set(body.overrides)
        )
        return {"notes": notes}

    @app.post("/rooms/{room_id}/replay")
    async def replay(
        room_id: str, body: ReplayIn, p: Annotated[str, Depends(person)]
    ) -> StreamingResponse:
        """Serve a recorded session again, labelled as a replay; nothing is written (NFR-8)."""
        from ..protocols.replay import recorded_run, replay_stream

        seated(room_id, p)
        viewer = body.viewer or p
        if body.session == "agent" and viewer != p:
            raise Denied("an agent session is private to its person")
        events = recorded_run(hub.journal, room_id, viewer, body.session, body.run_id)
        if not events:
            raise NotFound("no recorded run for that viewer and session")
        return StreamingResponse(
            replay_stream(events), media_type="text/event-stream", headers=SSE_HEADERS
        )

    # AG-UI ------------------------------------------------------------------------------------
    @app.post("/rooms/{room_id}/agui/feed")
    async def agui_feed(
        room_id: str, inp: RunAgentInput, s: Annotated[Session, Depends(current)]
    ) -> StreamingResponse:
        p = s.person
        room = seated(room_id, p)
        check_thread(inp, room_id, p, "feed")
        stream = feed_stream(
            room,
            p,
            inp,
            bus=hub.bus,
            journal=hub.journal,
            rotate_s=settings.feed_rotate_s,
            keepalive_s=settings.keepalive_s,
            alive=lambda: hub.store.session_live(s.sid, p),  # sign-out ends the stream
            prepare=lambda: ensure_surface(room, p),
        )
        return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/rooms/{room_id}/agui/agent")
    async def agui_agent(
        room_id: str, inp: RunAgentInput, s: Annotated[Session, Depends(current)]
    ) -> StreamingResponse:
        p = s.person
        room = seated(room_id, p)
        check_thread(inp, room_id, p, "agent")
        emit = Emitter(hub.journal, room.id, p, "agent", run_id=inp.run_id)
        stream = agent_session(
            room,
            p,
            inp,
            emit=emit,
            seen_messages=hub.seen_messages[f"{room_id}:{p}"],
            respond=hub.respond,
            after_decision=hub.after_decision,
            sid=s.sid,
        )
        return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)

    return app


def main() -> None:
    """Run the room server with settings from the environment (CONSILITH_*)."""
    import uvicorn

    settings = get_settings()
    hub = Hub(settings)
    if hub.dev_capability is not None:  # new at every start; the dev proxy reads it from here
        write_capability(settings.data_dir / "dev.env", hub.dev_capability)
    uvicorn.run(create_app(settings, hub), host=settings.host, port=settings.port)
