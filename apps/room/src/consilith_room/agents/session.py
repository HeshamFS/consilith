"""The AG-UI agent session: a person and their personal agent.

It carries text both ways, and it is where approvals live. A proposal into a lane the viewer
owns ends this run with an ``interrupt`` outcome; the next run must resume every open
interrupt. Invalid resumes are ``RUN_ERROR`` and change nothing (SPEC "AG-UI").
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from ag_ui.core import (
    Interrupt,
    RunAgentInput,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from consilith_contracts import Proposal, new_id

from ..domain.errors import RoomError
from ..domain.service import RoomService
from ..platform.keys import started_from
from ..protocols.agui import Emitter, run_error, started

log = logging.getLogger("consilith.agent_session")

APPROVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 1},
    },
    "required": ["approved", "reason"],
    "additionalProperties": False,
}


class Responder(Protocol):
    """What answers a person's message: the agent kernel (scripted model by default)."""

    def __call__(self, room: RoomService, person: str, text: str) -> Awaitable[str]: ...


AfterDecision = Callable[[RoomService, Proposal, bool], Awaitable[None]]


def interrupt_for(prop: Proposal) -> Interrupt:
    ops = ", ".join(f"{op.op} {op.path}" for op in prop.patch)
    return Interrupt(
        id=prop.interrupt_id or prop.id,
        reason="approval",
        message=f"{prop.author} proposes in your {prop.case}/{prop.target_lane} lane: {ops}. "
        f"Reason: {prop.reason}",
        response_schema=APPROVAL_SCHEMA,
        metadata={
            "proposalId": prop.id,
            "author": prop.author,
            "onBehalfOf": prop.on_behalf_of,
            "lane": f"{prop.case}/{prop.target_lane}",
            "patch": [op.wire() for op in prop.patch],
            "evidence": prop.evidence,
        },
    )


def _new_user_text(inp: RunAgentInput, seen: set[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for msg in inp.messages:
        data = msg.model_dump()
        if data.get("role") != "user" or data.get("id") in seen:
            continue
        content = data.get("content")
        text = (
            content
            if isinstance(content, str)
            else " ".join(str(p.get("text", "")) for p in content or [] if isinstance(p, dict))
        )
        out.append((str(data.get("id")), text))
    return out


async def agent_session(
    room: RoomService,
    viewer: str,
    inp: RunAgentInput,
    *,
    emit: Emitter,
    seen_messages: set[str],
    respond: Responder,
    after_decision: AfterDecision | None = None,
    sid: str | None = None,
) -> AsyncIterator[str]:
    """``sid`` is the signed-in session that started this run: a model call made while
    answering uses that session's key (platform/keys.py)."""
    yield emit(started(inp))
    open_props = {(p.interrupt_id or p.id): p for p in room.open_proposals(owner=viewer)}
    new_text = _new_user_text(inp, seen_messages)

    if inp.resume:
        ids = [r.interrupt_id for r in inp.resume]
        if len(ids) != len(set(ids)):
            yield emit(run_error("resume names an interrupt more than once", "duplicate_interrupt"))
            return
        unknown = [i for i in ids if i not in open_props]
        if unknown:
            already = all(_decided_by(room, i, viewer) for i in unknown)
            if not already:
                yield emit(
                    run_error(f"unknown interrupt id(s): {', '.join(unknown)}", "unknown_interrupt")
                )
                return
        missing = [i for i in open_props if i not in ids]
        if missing:
            yield emit(
                run_error(
                    f"resume must cover every open interrupt; missing {', '.join(missing)}",
                    "incomplete_resume",
                )
            )
            return
        decisions: list[tuple[Proposal, bool, str]] = []
        for entry in inp.resume:
            if entry.interrupt_id not in open_props or entry.status == "cancelled":
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            approved, reason = payload.get("approved"), payload.get("reason")
            if not isinstance(approved, bool) or not isinstance(reason, str) or not reason.strip():
                yield emit(
                    run_error(
                        f"interrupt {entry.interrupt_id} needs {{approved, reason}}",
                        "invalid_resume_payload",
                    )
                )
                return
            decisions.append((open_props[entry.interrupt_id], approved, reason))
        try:  # all or nothing: every approval is checked before any is committed
            room.decide_batch(
                viewer,
                [(p.id, a, r, f"resume:{p.interrupt_id}") for p, a, r in decisions],
            )
        except RoomError as exc:
            yield emit(run_error(exc.message, exc.code))
            return
        for prop, approved, reason in decisions:
            verb = "Accepted" if approved else "Declined"
            for chunk in _say(f"{verb} {prop.id} in {prop.case}/{prop.target_lane}: {reason}"):
                yield emit(chunk)
            pending = room.pending_followup(prop.id)
            if pending is not None and after_decision is not None:
                try:
                    await after_decision(room, prop, approved)
                    room.complete_followup(pending)
                except Exception:  # the decision stands; the follow-up failed
                    yield emit(
                        run_error(
                            _followup_failed(),
                            "server_fault",
                        )
                    )
                    return
        # A retried resume for an already-decided interrupt re-runs its pending follow-up.
        for entry in inp.resume:
            if entry.interrupt_id in open_props or after_decision is None:
                continue
            prop = _proposal_by_interrupt(room, entry.interrupt_id)
            pending = room.pending_followup(prop.id) if prop is not None else None
            if prop is not None and pending is not None:
                try:
                    await after_decision(room, prop, prop.status == "accepted")
                    room.complete_followup(pending)
                except Exception:
                    yield emit(
                        run_error(
                            _followup_failed(),
                            "server_fault",
                        )
                    )
                    return
    elif open_props and new_text:
        yield emit(
            run_error(
                "this thread has open approvals: answer them with resume first", "interrupts_open"
            )
        )
        return

    for mid, text in new_text:
        try:
            with started_from(sid):
                reply = await respond(room, viewer, text)
        except Exception:  # a server fault ends the run with RUN_ERROR, never silently
            yield emit(
                run_error(f"the agent failed to answer (error {_fault('respond')})", "server_fault")
            )
            return
        seen_messages.add(mid)  # only once it was really handled, so a retry is answered
        for chunk in _say(reply or "(no answer)"):
            yield emit(chunk)

    still_open = room.open_proposals(owner=viewer)
    if still_open:
        outcome = RunFinishedInterruptOutcome(interrupts=[interrupt_for(p) for p in still_open])
        yield emit(RunFinishedEvent(thread_id=inp.thread_id, run_id=inp.run_id, outcome=outcome))
    else:
        yield emit(
            RunFinishedEvent(
                thread_id=inp.thread_id, run_id=inp.run_id, outcome=RunFinishedSuccessOutcome()
            )
        )


def _decided_by(room: RoomService, interrupt_id: str, viewer: str) -> bool:
    """A repeated resume is a no-op only for the person who answered it the first time."""
    for node in (room.read("proposals") or {}).values():
        if node.get("interruptId") == interrupt_id and node.get("status") != "awaiting-owner":
            for dec in (room.read("decisions") or {}).values():
                if dec.get("proposal") == node.get("id"):
                    return dec.get("by") == viewer
    return False


def _say(text: str) -> list[TextMessageStartEvent | TextMessageContentEvent | TextMessageEndEvent]:
    mid = new_id("msg")
    return [
        TextMessageStartEvent(message_id=mid, role="assistant"),
        TextMessageContentEvent(message_id=mid, delta=text),
        TextMessageEndEvent(message_id=mid),
    ]


def _fault(where: str) -> str:
    """Log the exception server-side; the client gets only an opaque id (no exception text)."""
    ref = new_id("err")
    log.exception("agent session %s failed [%s]", where, ref)
    return ref


def _proposal_by_interrupt(room: RoomService, interrupt_id: str) -> Proposal | None:
    for node in (room.read("proposals") or {}).values():
        if node.get("interruptId") == interrupt_id:
            return Proposal.model_validate(node)
    return None


def _followup_failed() -> str:
    return f"follow-up after the decision failed (error {_fault('after_decision')})"
