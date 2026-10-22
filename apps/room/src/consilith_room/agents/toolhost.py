"""The tool pipeline every call runs through (BUILD_SPEC section 6, "Steps", item 5).

For each call the model asks for, ``ToolHost`` does, in this order:

1. map the alias to its ref, validate the arguments against the op's model-facing schema, reject
   any JSON number token, and run every free string through the prose guard;
2. resolve bindings and handles to concrete values, unit-checked through the lane validators;
3. the parse-confidence check: an ambiguous parse (no unit, a decimal comma, a range, a percent,
   pasted text) or a field that the number's left context does not name becomes a confirm card;
4. look up the op's class and check its minimum autonomy;
5. the taint rule: on a tainted turn any R1 or higher write becomes a confirm card;
6. ``require_pins``;
7. for edits and proposals, ``ruled_out``, reporting the stopping lesson;
8. the gate, run as ``Actor.agent_of(owner)``: the room service enforces lane ownership and the
   autonomy of the agent's drawer;
9. when approval is needed, store a pending action and end the run;
10. execute with ``op_id = <msg_id>:<callId>:<digest8(arguments)>``, so the same call is a no-op
    on retry and a different call is a new operation;
11. record: room log rows through the service, viewer-private journal events through the
    session, and a digest-only tool entry in ``chat_turns``;
12. return a fenced, size-capped result. A ``RoomError`` returns as a typed error.

The server assigns ``callId = <step>.<index>``. The model's own call id is only a pairing label.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from consilith_contracts import (
    PENDING_ACTION_TTL_S,
    Autonomy,
    PendingAction,
    PendingActionKind,
    PendingActionStatus,
    RiskClass,
    at_least,
    find_json_number,
    new_id,
    pending_action_digest,
)
from jsonschema import Draft202012Validator

from ..domain.actors import Grant
from ..domain.errors import Denied, RoomError
from ..domain.plugin_access import require_pins, room_pins
from ..domain.service import GRANT_TTL_S, RoomService
from ..platform.errors import ModelError
from . import adhoc_ui as _adhoc_ui  # noqa: F401  (registers ui.* and studio.compose)
from .cursor import announce
from .fence import fence
from .literal_parser import BindingError, card_reason_words
from .platform_tools import (
    CAP_WORDS,
    COUNTED_REFS,
    DEFAULT_CAPS,
    EVENT_DENIED_PREFIXES,
    OP_SPECS,
    PEER_REFS,
    OpContext,
    OpResult,
    OpSpec,
    Prepared,
    TurnState,
    catalog_entries,
    digest8,
    ruled_out_text,
)
from .prose_guard import prose_guard, substitute
from .status import acting
from .turn_events import (
    ApprovalRequest,
    ToolArgs,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEvent,
)

log = logging.getLogger("consilith.toolhost")

# The fixed profile a turn runs under; ``peer`` implies the catalog is peer.reply and
# proposal.create only (section 7).
ToolProfile = Literal["owner", "event", "peer"]

MODEL_RESULT_CAP = 6000  # characters of fenced result the model gets back
NOT_EXECUTED = "not executed: an earlier call in this step needs attention first"
REPEATED = "not executed: this repeats a call that already ran with the same arguments"
REPEATED_NUDGE = (
    "not executed: you already made this exact call in this turn. Use its result above and "
    "answer your person now, or do the next different step"
)
CARD_NOTE_CAP = 200
CARD_SHOWN_KEYS = ("capabilityLabel", "inputs")  # what `Prepared.display` may add to a card
TAINT_WHY = "this turn read text written by someone else, so changes need your approval"

# The pending-action kinds that this module can execute after approval. Later packages (the run
# supervisor, peer relay, lessons) register theirs here.
CardExecutor = Callable[[TurnState, PendingAction], "OpResult"]

# Executors registered once for every ToolHost, by card kind. A kind with none runs the op's own
# body on the stored call after approval, which is how every card but a run works.
CARD_EXECUTORS: dict[PendingActionKind, CardExecutor] = {}


def card_executor(kind: PendingActionKind) -> Callable[[CardExecutor], CardExecutor]:
    """Register how a card of ``kind`` runs after the person approves it."""

    def register(fn: CardExecutor) -> CardExecutor:
        CARD_EXECUTORS[kind] = fn
        return fn

    return register


@dataclass
class CallOutcome:
    """Everything the loop needs from one call: its events, what the model reads, and whether
    the rest of the step must not run."""

    result: ToolResult
    model_text: str
    events: list[TurnEvent] = field(default_factory=list[TurnEvent])
    approval: ApprovalRequest | None = None
    stop_step: bool = False  # a card, an error or a cap: later calls become "not executed"
    wrote: bool = False
    taint: bool = False


def _model_text(call_id: str, ref: str, body: dict[str, Any]) -> str:
    return fence(
        json.dumps({"tool": ref, **body}, default=str, separators=(",", ":")),
        source="tool",
        id=call_id,
        cap=MODEL_RESULT_CAP,
    )


def not_executed(call_id: str, ref: str, reason: str = NOT_EXECUTED) -> CallOutcome:
    """The fixed result of a call that follows a card, an error or a cap in the same step, or
    that repeats a call that already ran (``REPEATED``)."""
    result = ToolResult(call_id=call_id, status="not_executed", summary=reason)
    return CallOutcome(
        result=result,
        model_text=_model_text(call_id, ref, {"status": "not_executed", "message": reason}),
        events=[ToolStart(call_id=call_id, tool=ref, label=ref), ToolEnd(call_id), result],
    )


def card_of(action: PendingAction) -> dict[str, Any]:
    """The card's content, built by the server from the stored call (never from model text)."""
    resolved = action.resolved_args
    ref = str(action.call.get("ref", ""))
    card: dict[str, Any] = {"kind": action.kind.value, "tool": ref, "digest": action.digest}
    spec = OP_SPECS.get(ref)
    if spec is not None:
        card["label"] = spec.label
    edits = resolved.get("edits")
    if isinstance(edits, list):
        from .platform_tools import _display_edits  # pyright: ignore[reportPrivateUsage]

        card["edits"] = _display_edits(list(edits))
    for key in ("to", "case", "lane", "target_lane", "capability", "mode", "scope"):
        if key in resolved:
            card[key] = resolved[key]
    shown = action.call.get("shown")
    if isinstance(shown, dict):  # display-safe facts the server kept when it built the card
        card.update({k: v for k, v in shown.items() if k in CARD_SHOWN_KEYS})
    why = action.call.get("why")
    if why:
        card["why"] = str(why)
    elif action.taint:
        card["why"] = TAINT_WHY
    return card


def card_message(action: PendingAction) -> str:
    ref = str(action.call.get("ref", ""))
    label = OP_SPECS[ref].label if ref in OP_SPECS else ref
    return f"Confirm: {label}"


def card_note(action: PendingAction) -> str:
    """The agent's own words on a card, short and labelled untrusted by the interface."""
    resolved = action.resolved_args
    args = action.call.get("args")
    text = (
        (args.get("note") if isinstance(args, dict) else None)
        or resolved.get("note")
        or resolved.get("reason")
        or resolved.get("message")
        or resolved.get("text")
        or ""
    )
    return str(text)[:CARD_NOTE_CAP]


def approval_request(action: PendingAction) -> ApprovalRequest:
    return ApprovalRequest(
        action_id=action.id,
        kind=action.kind.value,
        message=card_message(action),
        card=card_of(action),
        note=card_note(action),
        expires=action.expires,
    )


def _revisions_now(room: RoomService, recorded: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in recorded:
        case, _, lane = key.partition("/")
        out[key] = str(room.read("revisions", case, lane) or "")
    return out


def action_digest(
    room: RoomService, action: PendingAction, revisions: dict[str, str] | None = None
) -> str:
    """The digest of a stored call over the lane revisions and plugin pins as they are now."""
    return pending_action_digest(
        kind=action.kind,
        room=action.room,
        person=action.person,
        ref=str(action.call.get("ref", "")),
        mode=str(action.call.get("mode", "")),
        resolved_args=action.resolved_args,
        input_revisions=_revisions_now(room, action.input_revisions)
        if revisions is None
        else revisions,
        plugin_pins=room_pins(room),
    )


def _prose_paths(schema: dict[str, Any], value: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string argument whose schema carries ``x-prose``, with its path."""
    out: list[tuple[str, str]] = []
    if schema.get("x-prose") and isinstance(value, str):
        out.append((path or "$", value))
    props = schema.get("properties")
    if isinstance(props, dict) and isinstance(value, dict):
        for key, sub in props.items():
            if key in value and isinstance(sub, dict):
                out += _prose_paths(sub, value[key], f"{path}.{key}" if path else key)
    items = schema.get("items")
    if isinstance(items, dict) and isinstance(value, list):
        for i, item in enumerate(value):
            out += _prose_paths(items, item, f"{path}[{i}]")
    return out


def _rewrite_prose(schema: dict[str, Any], value: Any, fix: Callable[[str], str]) -> Any:
    """A copy of ``value`` with ``fix`` applied to every ``x-prose`` string."""
    if schema.get("x-prose") and isinstance(value, str):
        return fix(value)
    props = schema.get("properties")
    if isinstance(props, dict) and isinstance(value, dict):
        return {
            k: _rewrite_prose(props[k], v, fix) if isinstance(props.get(k), dict) else v
            for k, v in value.items()
        }
    items = schema.get("items")
    if isinstance(items, dict) and isinstance(value, list):
        return [_rewrite_prose(items, v, fix) for v in value]
    return value


def result_display(room: RoomService) -> Callable[[str], str | None]:
    """``{{result:ID}}`` -> ``350 K (verified)``: value, unit and stamp from the stored result."""

    def show(rid: str) -> str | None:
        node = (room.read("results") or {}).get(rid)
        if node is None:
            return None
        value, unit = node.get("value"), node.get("unit", "")
        stamp, status = node.get("stamp", ""), node.get("status", "current")
        if node.get("kind", "number") == "number" and isinstance(value, int | float):
            what = f"{value:g} {unit}".strip()
        else:
            what = f"result {rid}"
        note = stamp if status == "current" else f"{stamp}, {status}"
        return f"{what} ({note})"

    return show


class ToolHost:
    """Validates, resolves bindings, authorizes, executes and records one tool call."""

    def __init__(
        self, *, clock: Callable[[], float] = time.time, caps: dict[str, int] | None = None
    ) -> None:
        self._clock = clock
        self.caps = {**DEFAULT_CAPS, **(caps or {})}
        # Later packages register how to run the pending action of their kind after approval.
        self.card_executors: dict[PendingActionKind, CardExecutor] = {}

    # the F4 entry point ----------------------------------------------------------------------
    async def run_call(
        self,
        room: RoomService,
        person: str,
        call: dict[str, Any],
        *,
        profile: ToolProfile = "owner",
        msg_id: str,
        call_id: str,
    ) -> ToolResult:
        """One call outside a loop, on a fresh turn: the catalog is the registry at the agent's
        autonomy, with no handles. ``run_call_full`` is what the loop uses."""
        turn = self.fresh_turn(room, person, msg_id, profile)
        return (await self.run_call_full(turn, call, call_id=call_id)).result

    def fresh_turn(
        self, room: RoomService, person: str, msg_id: str, profile: ToolProfile = "owner"
    ) -> TurnState:
        turn = TurnState(room=room, person=person, msg_id=msg_id, profile=profile)
        entries = catalog_entries(turn.autonomy, profile)
        turn.entries = {e.spec.alias: e for e in entries}
        return turn

    # the pipeline ----------------------------------------------------------------------------
    async def run_call_full(
        self,
        turn: TurnState,
        call: dict[str, Any],
        *,
        call_id: str,
        provider: str = "",
        model: str = "",
    ) -> CallOutcome:
        alias = str(call.get("tool", ""))
        args = call.get("args")
        args = args if isinstance(args, dict) else {}
        entry = turn.entries.get(alias)
        ref = entry.spec.ref if entry is not None else alias
        spec = OP_SPECS.get(ref) if entry is not None else None
        events: list[TurnEvent] = [
            ToolStart(
                call_id=call_id,
                tool=ref,
                label=spec.label if spec is not None else ref,
                provider=provider,
                model=model,
            )
        ]
        turn.counters.tool_calls += 1

        def fail(
            code: str, message: str, fix: str | None = None, *, stop: bool = True
        ) -> CallOutcome:
            result = ToolResult(
                call_id=call_id, status="error", summary=message, code=code, fix_action=fix
            )
            events.extend([ToolEnd(call_id), result])
            turn.tool_log.append({"callId": call_id, "ref": ref, "status": "error", "code": code})
            turn.persist()
            return CallOutcome(
                result=result,
                model_text=_model_text(
                    call_id, ref, {"status": "error", "code": code, "message": message}
                ),
                events=events,
                stop_step=stop,
            )

        # 1. alias, arguments, numbers, prose ------------------------------------------------
        if entry is None:
            return fail(
                "unknown_tool", f"there is no tool named {alias!r}; use one in the tool block"
            )
        if entry.availability.value != "available":
            fix = {
                "blocked_by_autonomy": "raise_autonomy",
                "blocked_by_drawer": "edit_drawer",
                "blocked_by_activation": "activate_plugin",
            }.get(entry.availability.value)
            return fail("tool_blocked", entry.reason or "that tool is not available to you", fix)
        if spec is None:
            return fail("not_available", f"{ref} is not available yet")
        if turn.profile == "peer" and ref not in PEER_REFS:
            return fail("not_in_profile", f"{ref} is not available in a peer turn")
        if turn.profile == "event" and ref.startswith(EVENT_DENIED_PREFIXES):
            return fail("not_in_profile", f"{ref} is not available in an event turn")
        where = find_json_number(args)
        if where is not None:
            return fail(
                "number_in_arguments",
                f"{where} is a number; never type one, use a binding (literal, result, derive)",
            )
        bad = _schema_error(spec, args)
        if bad is not None:
            return fail("bad_arguments", bad)
        gctx = turn.guard_context()
        for path, text in _prose_paths(spec.input_schema, args):
            checked = prose_guard(text, gctx)
            if not checked.ok:
                return fail("prose_guard", f"{path}: {checked.feedback()}")
        shown = turn.literals.display()
        lookup = result_display(turn.room)
        args = _rewrite_prose(
            spec.input_schema,
            args,
            lambda s: substitute(s, results=lookup, literals=shown, papers=turn.papers),
        )

        # 2. and 3. bindings, units, parse confidence ----------------------------------------
        try:
            prepared = (
                spec.prepare(turn, args) if spec.prepare is not None else Prepared(resolved=args)
            )
        except BindingError as exc:
            return fail(exc.code, exc.message)
        except RoomError as exc:
            return fail(exc.code, exc.message)
        events.append(ToolArgs(call_id=call_id, args=_display_args(args, prepared)))

        # 4. class and minimum autonomy ------------------------------------------------------
        if not at_least(turn.autonomy, spec.min_autonomy):
            return fail(
                "autonomy",
                f"{ref} needs the {spec.min_autonomy.value} setting in Your agent",
                "raise_autonomy",
            )
        # 6. pins ----------------------------------------------------------------------------
        try:
            require_pins(turn.room)
        except Denied as exc:
            return fail(exc.code, exc.message)
        # limits that a single call can hit --------------------------------------------------
        for name in (*(("writes",) if spec.writes else ()), *_counted(ref)):
            if getattr(turn.counters, name) >= self.caps[name]:
                return fail(
                    f"limit_{name}",
                    f"this turn reached its limit of {self.caps[name]} {CAP_WORDS[name]}",
                    "continue",
                )

        # 7. lessons that rule the change out ------------------------------------------------
        if prepared.patch:
            stopper = ruled_out_text(turn, prepared.patch)
            if stopper is not None:
                turn.mark_taint()  # lesson text can be written by someone else
                return fail(
                    "ruled_out",
                    "a lesson rules this change out: "
                    + fence(stopper, source="lesson", id="stopper", cap=500),
                )

        # The call is going ahead (run, or put on a card): the agent's cursor moves to it.
        announce(turn.room, turn.person, ref, {**prepared.resolved, **args})

        # 5. the taint rule, and which ops always need the owner -----------------------------
        reasons = list(prepared.reasons)
        whys = [card_reason_words(reasons)] if reasons else []
        if turn.taint and spec.writes:
            reasons.append("taint")
            whys.append(TAINT_WHY)
        if spec.risk is RiskClass.R3 or (
            spec.risk is RiskClass.R2 and not at_least(turn.autonomy, Autonomy.RUN_SOLVERS)
        ):
            reasons.append("class")
            whys.append("this action always needs your confirmation")
        why = "; ".join(whys)

        # 9. approval ------------------------------------------------------------------------
        if reasons:
            if spec.card_kind is None:
                return fail("not_yet", f"{ref} needs your confirmation, which is not available yet")
            action = self._make_card(turn, spec, args, prepared, why)
            events.extend([ToolEnd(call_id)])
            req = approval_request(action)
            events.append(req)
            result = ToolResult(
                call_id=call_id,
                status="awaiting_approval",
                summary="waiting for your approval",
                result_ids=(action.id,),
            )
            events.append(result)
            turn.tool_log.append(
                {
                    "callId": call_id,
                    "ref": ref,
                    "status": "card",
                    "digest": digest8(prepared.resolved),
                }
            )
            turn.persist()
            return CallOutcome(
                result=result,
                model_text=_model_text(
                    call_id,
                    ref,
                    {
                        "status": "awaiting_approval",
                        "message": "the person must confirm this on a card; do not repeat it",
                    },
                ),
                events=events,
                approval=req,
                stop_step=True,
            )

        # 8. and 10. the gate and the execution ----------------------------------------------
        op_id = f"{turn.msg_id}:{call_id}:{digest8(prepared.resolved)}"
        # The room sees what the agent is doing while the call runs ("reading a result",
        # "searching research papers"), not a bare "thinking" (Hesham, 2026-10-22).
        label = spec.label or ref
        async with acting(turn.room, turn.aid, "working", label[:1].lower() + label[1:]):
            return await self._execute(turn, spec, prepared.resolved, call_id, op_id, events)

    # execution -------------------------------------------------------------------------------
    async def _execute(
        self,
        turn: TurnState,
        spec: OpSpec,
        resolved: dict[str, Any],
        call_id: str,
        op_id: str,
        events: list[TurnEvent],
        grant: Grant | None = None,
    ) -> CallOutcome:
        ref = spec.ref

        def fail(code: str, message: str, fix: str | None = None) -> CallOutcome:
            result = ToolResult(
                call_id=call_id, status="error", summary=message, code=code, fix_action=fix
            )
            events.extend([ToolEnd(call_id), result])
            turn.tool_log.append({"callId": call_id, "ref": ref, "status": "error", "code": code})
            turn.persist()
            return CallOutcome(
                result=result,
                model_text=_model_text(
                    call_id, ref, {"status": "error", "code": code, "message": message}
                ),
                events=events,
                stop_step=True,
            )

        done = turn.ledger.get(op_id) if spec.writes else None  # reads just run again
        if done is not None:  # a retry of a call that already ran: no second effect
            res = OpResult(
                summary=str(done.get("summary", "already done")),
                data={"already": True},
                result_ids=tuple(done.get("ids", ())),
                wrote=bool(done.get("wrote")),
            )
        else:
            try:
                res = await spec.body(OpContext(turn, ref, call_id, op_id, grant), resolved)
            except RoomError as exc:
                return fail(exc.code, exc.message, _fix_for(exc))
            except BindingError as exc:
                return fail(exc.code, exc.message)
            except ModelError as exc:
                return fail(exc.code, exc.user_message, exc.fix_action)
            except Exception:  # a fault in an op: opaque to the model, logged for the operator
                ref_id = new_id("err")
                log.exception("op %s failed [%s]", ref, ref_id)
                return fail("server_fault", f"the tool failed (error {ref_id})")
            if res.wrote:
                turn.counters.writes += 1
            for name in _counted(ref):
                setattr(turn.counters, name, getattr(turn.counters, name) + 1)
            if spec.writes:
                turn.ledger[op_id] = {
                    "summary": res.summary,
                    "ids": list(res.result_ids),
                    "wrote": res.wrote,
                }
        if res.taint:
            turn.mark_taint()
        # 11. record: a digest-only entry --------------------------------------------------
        turn.tool_log.append(
            {
                "callId": call_id,
                "ref": ref,
                "status": "ok",
                "digest": digest8(resolved),
                "ids": list(res.result_ids),
            }
        )
        turn.persist()
        result = ToolResult(
            call_id=call_id, status="ok", summary=res.summary, result_ids=res.result_ids
        )
        events.extend([ToolEnd(call_id), result])
        # 12. a fenced, capped result for the model --------------------------------------------
        return CallOutcome(
            result=result,
            model_text=_model_text(
                call_id, ref, {"status": "ok", "summary": res.summary, "data": res.data}
            ),
            events=events,
            wrote=res.wrote,
            taint=res.taint,
        )

    def _make_card(
        self, turn: TurnState, spec: OpSpec, args: dict[str, Any], prepared: Prepared, why: str
    ) -> PendingAction:
        assert spec.card_kind is not None
        room = turn.room
        now = self._clock()
        action = PendingAction(
            id=new_id("act"),
            room=room.id,
            person=turn.person,
            msg_id=turn.msg_id,
            kind=spec.card_kind,
            call={
                "ref": spec.ref,
                "args": args,
                "mode": prepared.mode,
                "why": why,
                "shown": {k: v for k, v in prepared.display.items() if k in CARD_SHOWN_KEYS},
            },
            resolved_args=prepared.resolved,
            digest="0" * 64,
            input_revisions=dict(prepared.revisions),
            plugin_pins=room_pins(room),
            taint=turn.taint,
            origin=turn.origin,
            created=now,
            expires=now + PENDING_ACTION_TTL_S,
        )
        action = action.model_copy(
            update={"digest": action_digest(room, action, dict(prepared.revisions))}
        )
        room.store.pending_actions.add(action)
        return action

    # approvals -------------------------------------------------------------------------------
    async def execute_pending(
        self, turn: TurnState, action: PendingAction, *, call_id: str
    ) -> CallOutcome:
        """Run the stored call of a card the person approved. The caller has already won the
        ``pending -> executing`` compare-and-set. The digest is recomputed over the lane
        revisions and plugin pins as they are now; if they moved, nothing runs."""
        room, store = turn.room, turn.room.store.pending_actions
        ref = str(action.call.get("ref", ""))
        spec = OP_SPECS.get(ref)
        events: list[TurnEvent] = [
            ToolStart(call_id=call_id, tool=ref, label=spec.label if spec else ref),
            ToolArgs(call_id=call_id, args={"card": card_of(action)}),
        ]

        def refuse(code: str, message: str, fix: str | None = None) -> CallOutcome:
            store.set_status(
                room.id,
                turn.person,
                action.id,
                PendingActionStatus.EXECUTING,
                PendingActionStatus.CANCELLED,
                {"error": code, "message": message},
            )
            result = ToolResult(
                call_id=call_id, status="error", summary=message, code=code, fix_action=fix
            )
            events.extend([ToolEnd(call_id), result])
            return CallOutcome(
                result=result,
                model_text=_model_text(
                    call_id, ref, {"status": "error", "code": code, "message": message}
                ),
                events=events,
                stop_step=True,
            )

        try:
            require_pins(room)
        except Denied as exc:
            return refuse(exc.code, exc.message)
        if action_digest(room, action) != action.digest:
            return refuse(
                "inputs_changed",
                "the inputs changed after you were asked; ask your agent again",
                "ask_again",
            )
        if spec is None:
            return refuse("not_available", f"{ref} is not available")
        if not at_least(turn.autonomy, spec.min_autonomy):  # lowered since the card was made
            return refuse(
                "autonomy",
                f"{ref} needs the {spec.min_autonomy.value} setting in Your agent",
                "raise_autonomy",
            )
        op_id = f"{action.msg_id}:{action.id}"
        done: CallOutcome
        executor = self.card_executors.get(action.kind) or CARD_EXECUTORS.get(action.kind)
        if executor is not None:
            res = await _maybe_await(executor(turn, action))
            done = self._finish_card(turn, spec, call_id, op_id, res, events)
        else:
            # The op's own body runs on the stored call. The ledger makes a repeated resume
            # return the same result with no second effect. A run gets the grant the card is.
            grant = self._grant_for(turn, action)
            done = await self._execute(
                turn, spec, action.resolved_args, call_id, op_id, events, grant
            )
        status = (
            PendingActionStatus.DONE
            if done.result.status == "ok"
            else PendingActionStatus.CANCELLED
        )
        store.store_result(
            room.id,
            turn.person,
            action.id,
            {"status": done.result.status, "summary": done.result.summary},
            status,
        )
        return done

    def _grant_for(self, turn: TurnState, action: PendingAction) -> Grant | None:
        """The grant a confirmed run card mints. Only this path makes one, after the digest was
        recomputed over the revisions and pins as they are now."""
        if action.kind is not PendingActionKind.CONFIRM_RUN:
            return None
        args = action.resolved_args
        return Grant.of(
            "run",
            action.digest,
            turn.person,
            action.input_revisions,
            time.time() + GRANT_TTL_S,  # real time: the room checks it against real time
            case=str(args.get("case", "")),
            mode=str(args.get("mode", "")),
        )

    def _finish_card(
        self,
        turn: TurnState,
        spec: OpSpec,
        call_id: str,
        op_id: str,
        res: OpResult,
        events: list[TurnEvent],
    ) -> CallOutcome:
        turn.ledger[op_id] = {"summary": res.summary, "ids": list(res.result_ids)}
        if res.taint:
            turn.mark_taint()
        result = ToolResult(
            call_id=call_id, status="ok", summary=res.summary, result_ids=res.result_ids
        )
        events.extend([ToolEnd(call_id), result])
        turn.persist()
        return CallOutcome(
            result=result,
            model_text=_model_text(
                call_id, spec.ref, {"status": "ok", "summary": res.summary, "data": res.data}
            ),
            events=events,
        )


def _counted(ref: str) -> tuple[str, ...]:
    return (COUNTED_REFS[ref],) if ref in COUNTED_REFS else ()


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _fix_for(exc: RoomError) -> str | None:
    return {"denied": None, "lane_not_owned": "propose", "locked": "retry"}.get(exc.code)


def _schema_error(spec: OpSpec, args: dict[str, Any]) -> str | None:
    errors = sorted(
        Draft202012Validator(spec.input_schema).iter_errors(args), key=lambda e: list(e.path)
    )
    if not errors:
        return None
    first = errors[0]
    path = ".".join(str(p) for p in first.path) or "arguments"
    return f"{path}: {first.message[:160]}"


def _display_args(args: dict[str, Any], prepared: Prepared) -> dict[str, Any]:
    """The card's arguments for display: the resolved values and where each came from."""
    if prepared.display:
        return dict(prepared.display)
    return dict(args)
