"""The AG-UI agent session: a person and their personal agent.

It carries text both ways, and it is where approvals live. A proposal into a lane the viewer
owns ends this run with an ``interrupt`` outcome; the next run must resume every open
interrupt. Invalid resumes are ``RUN_ERROR`` and change nothing (SPEC "AG-UI").
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from ag_ui.core import (
    ActivitySnapshotEvent,
    BaseEvent,
    CustomEvent,
    Interrupt,
    RunAgentInput,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from consilith_contracts import (
    PendingAction,
    PendingActionStatus,
    Proposal,
    approval_response_schema,
    new_id,
    parse_approval,
)
from consilith_contracts.approvals import ApprovalPayloadError

from ..domain.errors import RoomError
from ..domain.service import RoomService
from ..platform.keys import started_from
from ..protocols.agui import Emitter, run_error, started
from .toolhost import approval_request
from .turn_events import (
    EVENT_NOTICE,
    Notice,
    Progress,
    Surface,
    TextDelta,
    ToolArgs,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEvent,
    TurnResume,
    TurnRunner,
)

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


# The reasons a one-tap decision of your own agent's proposal carries. The decision log still
# gets a reason (``decide_once`` requires one); the person may type their own instead.
SELF_APPROVED = "approved from my chat card"
SELF_DECLINED = "declined from my chat card"


def is_self_proposal(prop: Proposal) -> bool:
    """A proposal your own agent made into a lane you own: you decide it in one tap."""
    return prop.on_behalf_of == prop.lane_owner


def interrupt_for(prop: Proposal, cause: dict[str, Any] | None = None) -> Interrupt:
    """The interrupt of a proposal. ``cause`` is the peer cause record (``room.peer_cause``)
    when a peer message caused it: the card then says which message did."""
    ops = ", ".join(f"{op.op} {op.path}" for op in prop.patch)
    one_tap = is_self_proposal(prop)
    return Interrupt(
        id=prop.interrupt_id or prop.id,
        reason="approval",
        message=f"{prop.author} proposes in your {prop.case}/{prop.target_lane} lane: {ops}. "
        f"Reason: {prop.reason}",
        # The reason is optional on your own agent's proposal: one tap approves it.
        response_schema=approval_response_schema("confirm_edit") if one_tap else APPROVAL_SCHEMA,
        metadata={
            "kind": "proposal",
            "oneTap": one_tap,
            "proposalId": prop.id,
            "author": prop.author,
            "onBehalfOf": prop.on_behalf_of,
            "lane": f"{prop.case}/{prop.target_lane}",
            "patch": [op.wire() for op in prop.patch],
            "evidence": prop.evidence,
            **({"causedBy": _caused_by(cause)} if cause else {}),
        },
    )


def _caused_by(cause: dict[str, Any]) -> dict[str, str]:
    """The display-safe identity of the peer message behind a proposal: ids and the sender."""
    return {key: str(cause.get(key, "")) for key in ("thread", "msg", "from")}


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


MAX_MESSAGE_ID = 128


def interrupt_for_card(action: PendingAction) -> Interrupt:
    """The interrupt of a confirm card. Its payload is ``{approved}``, with an optional reason."""
    req = approval_request(action)
    return Interrupt(
        id=action.id,
        reason="approval",
        message=req.message,
        response_schema=approval_response_schema(action.kind.value),
        metadata={
            "kind": action.kind.value,
            "card": req.card,
            "note": req.note,
            "expires": req.expires,
            "origin": action.origin,
        },
    )


class _Sink:
    """Maps the turn runner's events to AG-UI events. Text is released per message after the
    guard, so a message opens on its first chunk and closes when another opens or the turn ends.
    Tool cards stream live. A ``Notice`` is a typed custom event, not an opaque ``RUN_ERROR``."""

    def __init__(self) -> None:
        self._open: str | None = None

    def close(self) -> list[BaseEvent]:
        if self._open is None:
            return []
        out: list[BaseEvent] = [TextMessageEndEvent(message_id=self._open)]
        self._open = None
        return out

    def map(self, event: TurnEvent) -> list[BaseEvent]:
        out: list[BaseEvent] = []
        if isinstance(event, TextDelta):
            if self._open != event.message_id:
                out += self.close()
                out.append(TextMessageStartEvent(message_id=event.message_id, role="assistant"))
                self._open = event.message_id
            out.append(TextMessageContentEvent(message_id=event.message_id, delta=event.text))
            return out
        out += self.close()
        if isinstance(event, ToolStart):
            out.append(
                ToolCallStartEvent(
                    tool_call_id=event.call_id,
                    tool_call_name=event.tool,
                    metadata={
                        "label": event.label,
                        "provider": event.provider,
                        "model": event.model,
                    },
                )
            )
        elif isinstance(event, ToolArgs):
            out.append(
                ToolCallArgsEvent(
                    tool_call_id=event.call_id, delta=json.dumps(event.args, default=str)
                )
            )
        elif isinstance(event, ToolEnd):
            out.append(ToolCallEndEvent(tool_call_id=event.call_id))
        elif isinstance(event, ToolResult):
            body = {
                "status": event.status,
                "summary": event.summary,
                "resultIds": list(event.result_ids),
                "code": event.code,
                "fixAction": event.fix_action,
            }
            out.append(
                ToolCallResultEvent(
                    message_id=f"res-{event.call_id}",
                    tool_call_id=event.call_id,
                    content=json.dumps(body),
                )
            )
        elif isinstance(event, Surface):
            out.append(
                ActivitySnapshotEvent(
                    message_id=f"a2ui-{event.surface_id}",
                    activity_type="a2ui-surface",
                    content={"surfaceId": event.surface_id, "messages": list(event.messages)},
                    replace=True,
                )
            )
        elif isinstance(event, Progress):
            out.append(
                CustomEvent(
                    name="consilith.progress",
                    value={
                        "message": event.message,
                        "callId": event.call_id,
                        "fraction": event.fraction,
                    },
                )
            )
        elif isinstance(event, Notice):
            out.append(
                CustomEvent(
                    name=EVENT_NOTICE,
                    value={
                        "code": event.code,
                        "message": event.message,
                        "fixAction": event.fix_action,
                    },
                )
            )
        # An ApprovalRequest becomes the interrupt outcome at the end of the run, and Done only
        # ends the open message (closed above): neither is an AG-UI event of its own.
        return out
        return out


def _newest_user_text(inp: RunAgentInput) -> list[tuple[str, str]]:
    """Only the newest user message is processed. The client's tools, context, state,
    forwarded props and every non-user message are ignored."""
    texts = _new_user_text(inp, set())
    texts = [(mid, text) for mid, text in texts if 0 < len(mid) <= MAX_MESSAGE_ID]
    return texts[-1:]


def _card_decided(room: RoomService, viewer: str, action_id: str) -> bool:
    found = room.store.pending_actions.get(room.id, viewer, action_id)
    return found is not None and found.status is not PendingActionStatus.PENDING


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
    runner: TurnRunner | None = None,
) -> AsyncIterator[str]:
    """``sid`` is the signed-in session that started this run: a model call made while
    answering uses that session's key (platform/keys.py).

    With a ``runner`` (the agent loop is on) the message goes to the turn runner, whose events
    are mapped here, and confirm cards are interrupts beside the open proposals. Without one,
    ``respond`` answers in one piece, as before."""
    yield emit(started(inp))
    open_props = {(p.interrupt_id or p.id): p for p in room.open_proposals(owner=viewer)}
    open_cards = (
        {a.id: a for a in room.store.pending_actions.open_for(room.id, viewer)}
        if runner is not None
        else {}
    )
    new_text = _newest_user_text(inp) if runner else _new_user_text(inp, seen_messages)
    answered_by_resume: set[str] = set()  # messages whose cards this run settles

    if inp.resume:
        ids = [r.interrupt_id for r in inp.resume]
        if len(ids) != len(set(ids)):
            yield emit(run_error("resume names an interrupt more than once", "duplicate_interrupt"))
            return
        unknown = [i for i in ids if i not in open_props and i not in open_cards]
        if unknown:
            already = all(
                _decided_by(room, i, viewer) or _card_decided(room, viewer, i) for i in unknown
            )
            if not already:
                yield emit(
                    run_error(f"unknown interrupt id(s): {', '.join(unknown)}", "unknown_interrupt")
                )
                return
        missing = [i for i in (*open_props, *open_cards) if i not in ids]
        if missing:
            yield emit(
                run_error(
                    f"resume must cover every open interrupt; missing {', '.join(missing)}",
                    "incomplete_resume",
                )
            )
            return
        card_entries: list[TurnResume] = []
        for entry in inp.resume:  # a card's payload fits its kind before anything is committed
            card = open_cards.get(entry.interrupt_id)
            if card is None:
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            if entry.status != "cancelled":
                try:
                    parse_approval(card.kind.value, payload)
                except ApprovalPayloadError as exc:
                    yield emit(run_error(str(exc), "invalid_resume_payload"))
                    return
            card_entries.append(
                TurnResume(
                    interrupt_id=entry.interrupt_id,
                    status="cancelled" if entry.status == "cancelled" else "resolved",
                    payload=payload,
                )
            )
        decisions: list[tuple[Proposal, bool, str]] = []
        for entry in inp.resume:
            if entry.interrupt_id not in open_props or entry.status == "cancelled":
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            approved, reason = payload.get("approved"), payload.get("reason")
            if is_self_proposal(open_props[entry.interrupt_id]) and isinstance(approved, bool):
                typed = reason.strip() if isinstance(reason, str) else ""
                reason = typed or (SELF_APPROVED if approved else SELF_DECLINED)
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
        failed: list[bool] = []
        by_msg: dict[str, list[TurnResume]] = {}
        for r in card_entries:
            by_msg.setdefault(open_cards[r.interrupt_id].msg_id, []).append(r)
        answered_by_resume.update(by_msg)
        for card_msg, entries in by_msg.items():
            assert runner is not None
            async for out in _run_runner(
                emit, room, viewer, runner, card_msg, "", entries, sid, failed
            ):
                yield out
            if failed:
                return
    elif open_props and new_text:
        yield emit(
            run_error(
                "this thread has open approvals: answer them with resume first", "interrupts_open"
            )
        )
        return

    for mid, text in new_text:
        if mid in answered_by_resume:
            continue  # the page re-sent the message a card came from: its reply was just given
        if runner is not None:
            failed = []
            async for out in _run_runner(emit, room, viewer, runner, mid, text, None, sid, failed):
                yield out
            if failed:
                return
            seen_messages.add(mid)
            continue
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
    cards_open = room.store.pending_actions.open_for(room.id, viewer) if runner is not None else []
    if still_open or cards_open:
        outcome = RunFinishedInterruptOutcome(
            interrupts=[
                *(interrupt_for(p, room.peer_cause(p.id)) for p in still_open),
                *(interrupt_for_card(a) for a in cards_open),
            ]
        )
        yield emit(RunFinishedEvent(thread_id=inp.thread_id, run_id=inp.run_id, outcome=outcome))
    else:
        yield emit(
            RunFinishedEvent(
                thread_id=inp.thread_id, run_id=inp.run_id, outcome=RunFinishedSuccessOutcome()
            )
        )


async def _run_runner(
    emit: Emitter,
    room: RoomService,
    viewer: str,
    runner: TurnRunner,
    msg_id: str,
    text: str,
    resume: list[TurnResume] | None,
    sid: str | None,
    failed: list[bool],
) -> AsyncIterator[str]:
    """Run one turn on the runner and emit its events as AG-UI. A fault ends the run with a
    ``RUN_ERROR`` and an opaque reference, never with exception text."""
    sink = _Sink()
    try:
        async for event in runner.run_turn(room, viewer, msg_id, text, resume=resume, sid=sid):
            for out in sink.map(event):
                yield emit(out)
        for out in sink.close():
            yield emit(out)
    except Exception:
        failed.append(True)
        yield emit(
            run_error(f"the agent failed to answer (error {_fault('respond')})", "server_fault")
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
