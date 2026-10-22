"""The ops registry: what the agent can do (BUILD_SPEC section 6, "Ops inventory").

Each op registers with the ``@op`` decorator: its dotted ref, its risk class and the least
autonomy that may run it. The tool host looks the op up here, validates the model's arguments
against the op's schema, resolves bindings, applies the gate, and runs the body. The catalog
(WS-C) renders the same registry to the model, so an op the registry does not hold cannot be
called.

This module registers the R0 reads and the R1 writes of the loop's first milestone. Other
packages register theirs through the same decorator (``ui.*``, ``studio.compose``,
``catalog.*``, ``skill.read``, ``kb.read``, ``peer.*``). R4 ops are never registered: ``op``
refuses them, and ``R4_REFS`` names the ones the spec lists, so a test can prove each is absent.

An op body is ``async def body(ctx, args) -> OpResult``. ``args`` are the arguments the tool
host resolved from the model's bindings and handles, so a body sees concrete values only. A body
returns server-built facts, and anything authored by another person is returned fenced and sets
the turn's taint.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from consilith_contracts import (
    Autonomy,
    Binding,
    BindingKind,
    CatalogEntry,
    PatchOp,
    RiskClass,
    ToolKind,
    ToolSpec,
    at_least,
)
from consilith_contracts.agent_tools import Availability
from consilith_contracts.approvals import PendingActionKind
from consilith_contracts.cases import CaseDef, FieldDef, SpecError, validate_lane
from consilith_contracts.chat import TurnRole
from consilith_contracts.results import ResultRecord
from consilith_contracts.room import Proposal

from ..domain.actors import Actor, Grant, agent_id
from ..domain.errors import Denied, LaneNotOwned, NotFound, RoomError
from ..domain.paths import pointer
from ..domain.service import RoomService
from .fence import fence
from .literal_parser import (
    BindingError,
    LiteralSet,
    Resolved,
    card_reason_words,
    resolve_number,
)
from .prose_guard import GuardContext

OpBody = Callable[..., Awaitable[Any]]
ToolProfile = Literal["owner", "event", "peer"]

# Op ref -> its body, and op ref -> everything the host needs to run it.
OPS: dict[str, OpBody] = {}
OP_SPECS: dict[str, OpSpec] = {}

# R4: never offered to an agent (spec section 6). ``op`` refuses every one of these refs, and no
# op may carry the R4 class, so none can reach the catalog.
R4_REFS = frozenset(
    {
        "proposal.decide",
        "proposal.accept",
        "proposal.decline",
        "warning.acknowledge",
        "agent.config",
        "agent.config.set",
        "plugin.activate",
        "room.invite",
        "room.create",
        "room.delete",
        "keys.set",
        "keys.put",
        "chatgpt.login",
        "replay.run",
        "solvers.warm",
        "solvers.discover",
        "reproduce",
        "room_agent.tool",
        "artifact.upload",
        "case.switch",
        "analysis.handcalc",
        "tool.run",
        "run_tool",
        "tool.explore",
    }
)

# What each profile may call, besides the registry's own checks. A peer turn is fixed to these
# two (section 7); an event turn (a brought agent's reply to a room event) never starts runs,
# sends peer messages, claims lanes or writes lessons.
# Peer turns may read results (read-only), so an agent can check what a colleague handed over
# instead of asking back (found live, 2026-10-22).
PEER_REFS = frozenset({"peer.reply", "proposal.create", "result.read", "result.list"})
EVENT_DENIED_PREFIXES = ("run.start", "peer.", "lane.claim", "lane.release", "lesson.")

# Per-turn caps the tool host enforces on counted ops (the same for every provider), and which
# op counts against which cap. ``writes`` counts every op that changes room state.
DEFAULT_CAPS = {"writes": 3, "runs": 1, "peer_sends": 2}
COUNTED_REFS = {"run.start": "runs", "peer.send": "peer_sends"}
CAP_WORDS = {"writes": "changes", "runs": "run", "peer_sends": "messages to a colleague's agent"}

MAX_EDITS = 10
RESULT_ROWS = 40


# --- turn state ----------------------------------------------------------------------------


@dataclass
class Counters:
    """The per-turn limits' counters. They are keyed by ``msg_id`` and persisted, so a confirm
    card's resume keeps counting from where the turn stopped."""

    model_calls: int = 0
    tool_calls: int = 0
    writes: int = 0
    runs: int = 0
    peer_sends: int = 0
    elapsed_s: float = 0.0  # wall clock spent, not counting time waiting for an approval

    def to_dict(self) -> dict[str, float]:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(data: dict[str, Any] | None) -> Counters:
        known = Counters().__dict__
        return Counters(**{k: type(known[k])(v) for k, v in (data or {}).items() if k in known})


@dataclass
class TurnState:
    """Everything one tool call needs to know about its turn."""

    room: RoomService
    person: str
    msg_id: str
    profile: ToolProfile = "owner"
    literals: LiteralSet = field(default_factory=LiteralSet)
    entries: dict[str, CatalogEntry] = field(default_factory=dict[str, CatalogEntry])
    taint: bool = False
    counters: Counters = field(default_factory=Counters)
    kernel: Any = None  # the Kernel: ``ruled_out`` and ``send_proposal``
    propose: Callable[..., Awaitable[str | None]] | None = None
    briefing: Callable[[RoomService, str, str], dict[str, Any]] | None = None
    ledger: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    persist: Callable[[], None] = lambda: None
    origin: Literal["owner", "event"] = "owner"
    seen_results: set[str] = field(default_factory=set[str])
    # Papers this turn's research search returned: id to its citation line, for {{paper:ID}}.
    papers: dict[str, str] = field(default_factory=dict[str, str])
    # Digest-only record of each call (callId, ref, status, argument digest), kept in
    # ``chat_turns.meta``. Never arguments or results.
    tool_log: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    @property
    def aid(self) -> str:
        return agent_id(self.person)

    @property
    def actor(self) -> Actor:
        return Actor.agent_of(self.person)

    @property
    def autonomy(self) -> Autonomy:
        return self.room.agent_config(self.aid).autonomy

    def mark_taint(self) -> None:
        if not self.taint:
            self.taint = True
            self.persist()

    def guard_context(self, *, owner_chat: bool = False) -> GuardContext:
        """What the prose guard may allow in this turn: ids the server knows."""
        room = self.room
        results = frozenset(str(k) for k in (room.read("results") or {}))
        ids: set[str] = set(results)
        for cid, cdef in room.case_definitions().items():
            ids.add(cid)
            for lane in cdef.lanes:
                ids.add(lane.id)
                ids.add(f"{cid}/{lane.id}")
                ids.update(f.name for f in lane.fields)
        for plugin in room.read("room", "plugins") or []:
            ids.add(str(plugin.get("id", "")))
        for alias, entry in self.entries.items():
            ids.add(alias)
            ids.add(entry.spec.ref)
        ids.update(OP_SPECS)
        ids.update(alias_for(s.ref, s.kind) for s in OP_SPECS.values())
        ids.update(self.literals.ids())
        ids.discard("")
        return GuardContext(
            ids=frozenset(ids),
            result_ids=results,
            lit_ids=self.literals.ids(),
            paper_ids=frozenset(self.papers),
            owner_chat=owner_chat,
        )


@dataclass
class OpContext:
    """One call of one op."""

    turn: TurnState
    ref: str
    call_id: str
    op_id: str
    grant: Grant | None = None  # set only when a confirmed card runs this call (run.start)

    @property
    def room(self) -> RoomService:
        return self.turn.room

    @property
    def actor(self) -> Actor:
        """The agent acting for the person, carrying the card's grant when there is one."""
        return self.turn.actor.with_grant(self.grant)

    @property
    def person(self) -> str:
        return self.turn.person


@dataclass
class OpResult:
    """What an op body returns. ``data`` is for the model (the host fences and caps it);
    ``summary`` is the server-built line for the tool card."""

    summary: str
    data: dict[str, Any] = field(default_factory=dict[str, Any])
    result_ids: tuple[str, ...] = ()
    taint: bool = False  # the data holds class-B text
    wrote: bool = False


@dataclass
class Prepared:
    """A call after step 2 and step 3: concrete arguments, why it may need a card, and how to
    show it."""

    resolved: dict[str, Any]
    reasons: tuple[str, ...] = ()
    display: dict[str, Any] = field(default_factory=dict[str, Any])
    revisions: dict[str, str] = field(default_factory=dict[str, str])
    mode: str = ""  # a run's mode; empty for other ops
    # The patch an edit or proposal would write, so the host can run ``ruled_out``.
    patch: list[PatchOp] = field(default_factory=list[PatchOp])


Prepare = Callable[[TurnState, dict[str, Any]], Prepared]


@dataclass(frozen=True)
class OpSpec:
    ref: str
    risk: RiskClass
    min_autonomy: Autonomy
    body: OpBody
    kind: ToolKind = ToolKind.PLATFORM
    description: str = ""
    label: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict[str, Any])
    writes: bool = False  # changes room state, so a tainted turn needs a card first
    prepare: Prepare | None = None
    card_kind: PendingActionKind | None = None  # the confirm kind a card for this op uses


def _kind_of(ref: str) -> ToolKind:
    if ref.startswith("ui."):
        return ToolKind.UI
    if ref.startswith("peer."):
        return ToolKind.PEER
    return ToolKind.PLATFORM


def alias_for(ref: str, kind: ToolKind | None = None) -> str:
    """The provider-safe name the model uses: ``<kind>_<slug>_<6-char hash of the ref>``.

    The hash comes from the canonical ref alone, so every provider sees the same alias. The
    kind follows the ref (``ui.*`` and ``peer.*`` have their own) unless one is given. The one
    implementation is the catalog's, so the name the loop expects is the name the catalog shows."""
    from .catalog import alias_for as catalog_alias  # the catalog imports this module

    return catalog_alias(ref, kind or _kind_of(ref))


def op(
    ref: str,
    *,
    risk: RiskClass,
    min_autonomy: Autonomy,
    schema: dict[str, Any] | None = None,
    description: str = "",
    label: str = "",
    writes: bool | None = None,
    prepare: Prepare | None = None,
    card_kind: PendingActionKind | None = None,
    kind: ToolKind | None = None,
) -> Callable[[OpBody], OpBody]:
    """Register an op body under its dotted ref with its risk class and minimum autonomy.

    ``writes`` defaults to true for every class above R0. R4 ops and the refs in ``R4_REFS`` are
    refused: they are never offered to an agent.
    """
    if risk is RiskClass.R4 or ref in R4_REFS:
        raise ValueError(f"{ref} is R4: never offered to an agent")
    if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", ref):
        raise ValueError(f"{ref!r} is not a dotted op ref")

    def register(body: OpBody) -> OpBody:
        if ref in OPS and OPS[ref] is not body:
            raise ValueError(f"op {ref} is already registered")
        OPS[ref] = body
        OP_SPECS[ref] = OpSpec(
            ref=ref,
            risk=risk,
            min_autonomy=min_autonomy,
            body=body,
            kind=kind or _kind_of(ref),
            description=description or (body.__doc__ or "").strip().split("\n")[0],
            label=label or ref,
            input_schema=schema or {"type": "object", "additionalProperties": False},
            writes=(risk is not RiskClass.R0) if writes is None else writes,
            prepare=prepare,
            card_kind=card_kind,
        )
        return body

    return register


def _tool_spec(ref: str, spec: OpSpec) -> ToolSpec:
    return ToolSpec(
        ref=ref,
        alias=alias_for(ref, spec.kind),
        kind=spec.kind,
        description=spec.description,
        input_schema=spec.input_schema,
        risk=spec.risk,
        min_autonomy=spec.min_autonomy,
    )


def op_specs() -> list[ToolSpec]:
    """Every registered op as a tool spec. The catalog (``catalog.py``) reads this to list the
    platform ops next to its own reads, plugin tools, skills and knowledge."""
    return [_tool_spec(ref, spec) for ref, spec in sorted(OP_SPECS.items())]


def catalog_entries(autonomy: Autonomy, profile: ToolProfile = "owner") -> list[CatalogEntry]:
    """The registry as catalog entries for one agent: what the loop uses until the full
    ``ToolCatalog`` (WS-C) takes over. An op above the agent's autonomy stays listed, blocked,
    with the reason."""
    out: list[CatalogEntry] = []
    for ref, spec in sorted(OP_SPECS.items()):
        if profile == "peer" and ref not in PEER_REFS:
            continue
        if profile == "event" and ref.startswith(EVENT_DENIED_PREFIXES):
            continue
        tool = _tool_spec(ref, spec)
        if at_least(autonomy, spec.min_autonomy):
            out.append(CatalogEntry(spec=tool))
        else:
            out.append(
                CatalogEntry(
                    spec=tool,
                    availability=Availability.BLOCKED_BY_AUTONOMY,
                    reason=f"needs the {spec.min_autonomy.value} setting in Your agent",
                )
            )
    return out


def render_tool_block(entries: list[CatalogEntry]) -> str:
    """A plain tool block: alias, risk and schema for each available tool."""
    lines = ["TOOLS (call by alias)"]
    for e in entries:
        s = e.spec
        if e.availability is Availability.AVAILABLE:
            schema = json.dumps(s.input_schema, sort_keys=True, separators=(",", ":"))
            lines.append(f"- {s.alias} [{s.risk.value}] {s.description} args={schema}")
        else:
            lines.append(f"- {s.alias} [{s.risk.value}] unavailable: {e.reason}")
    return "\n".join(lines)


def digest8(value: Any) -> str:
    """Eight hex characters of the canonical JSON of ``value``: the argument digest of an op id."""
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


# --- shared helpers ------------------------------------------------------------------------

_BINDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": [k.value for k in BindingKind],
            "description": "where the number comes from; never type a number",
        },
        "ref": {"type": ["string", "null"], "maxLength": 200},
        "op": {"type": ["string", "null"], "enum": ["scale", "offset", None, ""]},
        "by": {"type": ["string", "null"], "maxLength": 200},
    },
    "required": ["kind"],
    "additionalProperties": False,
}
_EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "case": {"type": "string", "maxLength": 128},
        "lane": {"type": "string", "maxLength": 128},
        "field": {"type": "string", "maxLength": 128},
        "value": _BINDING_SCHEMA,
        "text": {"type": "string", "maxLength": 500, "x-prose": True},
    },
    "required": ["case", "lane", "field"],
    "additionalProperties": False,
}
_ID: dict[str, Any] = {"type": "string", "maxLength": 200}


def _case_lane_field(
    room: RoomService, case: str, lane: str, name: str
) -> tuple[CaseDef, FieldDef]:
    cdef = room.case_definitions().get(case)
    if cdef is None:
        raise BindingError("unknown_case", f"no case {case!r} in this room")
    ldef = cdef.lane(lane)
    if ldef is None:
        raise BindingError("unknown_lane", f"case {case} has no lane {lane!r}")
    fdef = next((f for f in ldef.fields if f.name == name), None)
    if fdef is None:
        raise BindingError("unknown_field", f"lane {case}/{lane} has no field {name!r}")
    return cdef, fdef


def _result_lookup(room: RoomService) -> Callable[[str], ResultRecord | None]:
    def lookup(rid: str) -> ResultRecord | None:
        node = (room.read("results") or {}).get(rid)
        return None if node is None else ResultRecord.model_validate(node)

    return lookup


def _live_lookup(room: RoomService) -> Callable[[str], dict[str, Any] | None]:
    def lookup(path: str) -> dict[str, Any] | None:
        parts = [p for p in path.strip("/").split("/") if p]
        if parts and parts[0] == "twin":
            parts = parts[1:]
        if len(parts) != 3:
            return None
        node = room.read("twin", *parts)
        return node if isinstance(node, dict) else None

    return lookup


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else exc.__class__.__name__


def _binding(raw: Any, name: str) -> Binding:
    if raw is None:
        raise BindingError("bad_arguments", f"{name} needs a value binding")
    try:
        return Binding.model_validate(raw)
    except ValueError as exc:
        raise BindingError("bad_binding", _first_line(exc)) from exc


def resolve_edits(
    turn: TurnState, edits: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[PatchOp], tuple[str, ...], dict[str, str]]:
    """Step 2 and 3 for a list of lane edits: concrete values, the patch, the reasons for a
    card and the lane revisions the edits read. Raises ``BindingError``."""
    room = turn.room
    if not edits:
        raise BindingError("bad_arguments", "name at least one edit")
    if len(edits) > MAX_EDITS:
        raise BindingError("too_many_edits", f"at most {MAX_EDITS} edits in one call")
    resolved: list[dict[str, Any]] = []
    patch: list[PatchOp] = []
    reasons: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    new_values: dict[tuple[str, str], dict[str, Any]] = {}
    results = _result_lookup(room)
    live = _live_lookup(room)
    for edit in edits:
        case, lane, name = str(edit["case"]), str(edit["lane"]), str(edit["field"])
        if (case, lane, name) in seen:
            raise BindingError("duplicate_edit", f"{case}/{lane}/{name} is set twice in one call")
        seen.add((case, lane, name))
        _, fdef = _case_lane_field(room, case, lane, name)
        value: Any
        echo_reasons: tuple[str, ...] = ()
        if fdef.kind in ("list", "object"):
            raise BindingError(
                "field_not_writable",
                f"{name} is a {fdef.kind} field: say what you would suggest, the person sets it",
            )
        if fdef.kind in ("number", "integer"):
            got: Resolved = resolve_number(
                _binding(edit.get("value"), name),
                fdef,
                literals=turn.literals,
                results=results,
                live=live,
            )
            value, source, echo, echo_reasons = got.value, got.source, got.echo, got.reasons
        elif fdef.kind == "choice":
            binding = _binding(edit.get("value"), name)
            if binding.kind is not BindingKind.CHOICE or binding.ref not in fdef.choices:
                raise BindingError(
                    "bad_choice", f"{name} takes a choice binding naming one of: {fdef.choices}"
                )
            value, source, echo = binding.ref, f"choice {binding.ref}", f"choice {binding.ref}"
        else:  # text: model text, already through the prose guard; digits only as {{lit:N}}
            text = edit.get("text")
            if not isinstance(text, str) or not text.strip():
                raise BindingError("bad_arguments", f"{name} needs text")
            value, source, echo = text, "model text", "text from your agent"
        resolved.append(
            {
                "case": case,
                "lane": lane,
                "field": name,
                "path": pointer("twin", case, lane, name),
                "value": value,
                "source": source,
                "echo": echo,
                "reasons": list(echo_reasons),
            }
        )
        reasons.extend(echo_reasons)
        new_values.setdefault((case, lane), {})[name] = value
        patch.append(PatchOp(op="add", path=pointer("twin", case, lane, name), value=value))
    revisions: dict[str, str] = {}
    for (case, lane), values in new_values.items():
        candidate = {**(room.read("twin", case, lane) or {}), **values}
        try:
            validate_lane(room.case_definitions()[case], lane, candidate)
        except SpecError as exc:
            raise BindingError("spec_invalid", f"{exc.field}: {exc.message}") from exc
        revisions[f"{case}/{lane}"] = str(room.read("revisions", case, lane) or "")
    return resolved, patch, tuple(dict.fromkeys(reasons)), revisions


def edit_reason(resolved: list[dict[str, Any]]) -> str:
    """The server-generated reason of an edit, from the person's own words."""
    parts: list[str] = []
    for e in resolved:
        echo = str(e["echo"])
        if e.get("source") == "model text":  # no words of the person's to quote
            parts.append("text your agent wrote")
        else:
            parts.append(echo if echo.startswith("derived") else f"chat: '{echo}'")
    return "; ".join(dict.fromkeys(parts))[:400] or "chat edit"


def _display_edits(resolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in resolved:
        v = e["value"]
        shown = f"{v['v']:g} {v['unit']}".strip() if isinstance(v, dict) else str(v)
        out.append(
            {
                "path": f"{e['case']}/{e['lane']}/{e['field']}",
                "value": shown,
                "source": e["source"],
                "from": e["echo"],
            }
        )
    return out


def _prepare_edits(turn: TurnState, args: dict[str, Any]) -> Prepared:
    resolved, patch, reasons, revisions = resolve_edits(turn, list(args.get("edits") or []))
    return Prepared(
        resolved={"edits": resolved, "reason": edit_reason(resolved)},
        reasons=reasons,
        display={"edits": _display_edits(resolved), "why": card_reason_words(reasons)},
        revisions=revisions,
        patch=patch,
    )


def _owner_of(room: RoomService, case: str, lane: str) -> str | None:
    owner = room.lane_owners().get(case, {}).get(lane)
    return None if owner is None else str(owner)


def _proposal_data(
    case: str, lane: str, patch: list[PatchOp], reason: str, evidence: list[str]
) -> dict[str, Any]:
    return {
        "case": case,
        "targetLane": lane,
        "reason": reason,
        "evidence": evidence,
        "patch": [p.wire() for p in patch],
    }


async def send_proposal(
    turn: TurnState,
    case: str,
    lane: str,
    patch: list[PatchOp],
    reason: str,
    evidence: list[str],
    op_id: str,
) -> str:
    """Propose to a lane's owner and return the proposal id. It goes through the conductor over
    A2A when the kernel has that path, else straight to the service."""
    room, aid = turn.room, turn.aid
    before = {p.id for p in room.open_proposals()}
    sender = turn.propose
    if sender is None and turn.kernel is not None:
        sender = getattr(turn.kernel, "send_proposal", None)
    if sender is None:
        return room.propose(turn.actor, case, lane, patch, reason, evidence, op_id=op_id).id
    await sender(room, aid, _proposal_data(case, lane, patch, reason, evidence))
    mine = [
        p
        for p in room.open_proposals()
        if p.id not in before and p.author == aid and p.case == case and p.target_lane == lane
    ]
    if mine:
        return mine[-1].id
    raise NotFound("the proposal was not recorded")


def ruled_out_text(turn: TurnState, patch: list[PatchOp]) -> str | None:
    """The lesson that rules this patch out (FR-MEM-2), noted in the room as the kernel does."""
    kernel = turn.kernel
    if kernel is None or not hasattr(kernel, "ruled_out"):
        return None
    stopper = kernel.ruled_out(turn.room, turn.aid, patch)
    if stopper is not None:
        turn.room.note(
            turn.actor, "agent.lesson.applied", {"agent": turn.aid, "lesson": str(stopper)}
        )
    return None if stopper is None else str(stopper)


# --- R0: reads -----------------------------------------------------------------------------


@op(
    "briefing.read",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {"section": {"type": "string", "maxLength": 40}},
        "additionalProperties": False,
    },
    description="Read one section of the briefing again (me, lanes, twin, results, runs, inbox).",
    label="Read the briefing",
)
async def briefing_read(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    build = ctx.turn.briefing
    if build is None:
        raise RoomError("the briefing is not available yet", code="not_available")
    try:
        data = build(ctx.room, ctx.person, "owner")
    except NotImplementedError as exc:
        raise RoomError("the briefing is not available yet", code="not_available") from exc
    section = str(args.get("section") or "")
    if section:
        if section not in data:
            raise RoomError(
                f"no briefing section {section!r}; sections: {sorted(data)}",
                code="unknown_section",
            )
        data = {section: data[section]}
    return OpResult(summary=f"briefing {section or 'all'}", data=data)


@op(
    "lane.read",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {"case": _ID, "lane": _ID},
        "required": ["case", "lane"],
        "additionalProperties": False,
    },
    description="Read one lane of the twin: its values, owner and revision.",
    label="Read a lane",
)
async def lane_read(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    room, case, lane = ctx.room, str(args["case"]), str(args["lane"])
    cdef = room.case_definitions().get(case)
    ldef = None if cdef is None else cdef.lane(lane)
    if cdef is None or ldef is None:
        raise NotFound(f"no lane {case}/{lane}")
    owner = _owner_of(room, case, lane)
    values: dict[str, Any] = dict(room.read("twin", case, lane) or {})
    foreign_text = False
    if owner != ctx.person:
        for f in ldef.fields:
            if f.kind in ("text", "list", "object") and f.name in values:
                foreign_text = True
                values[f.name] = fence(
                    json.dumps(values[f.name]), source="lane", id=f"{case}/{lane}/{f.name}"
                )
    return OpResult(
        summary=f"read {case}/{lane}",
        data={
            "case": case,
            "lane": lane,
            "owner": owner,
            "mine": owner == ctx.person,
            "revision": room.read("revisions", case, lane),
            "values": values,
            "fields": [
                {"name": f.name, "kind": f.kind, "unit": f.unit, "choices": f.choices}
                for f in ldef.fields
            ],
        },
        taint=foreign_text,
    )


_STATUS_WHY = {
    "stale": "an input lane changed after this run",
    "superseded": "a newer or higher-tier result replaced it",
    "failed": "the run failed",
    "timeout": "the run timed out",
}


def _result_row(rec: ResultRecord) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": rec.id,
        "case": rec.case,
        "quantity": rec.quantity,
        "kind": rec.kind,
        "stamp": rec.stamp.value,
        "status": rec.status.value,
        "tier": int(rec.tier),
        "runId": rec.run_id,
    }
    if rec.kind == "number":
        row["value"], row["unit"] = rec.value, rec.unit
    if rec.status.value in _STATUS_WHY:
        row["why"] = _STATUS_WHY[rec.status.value]
    return row


# What result.read shows of a result's content: enough to answer questions about it (found live:
# the agent could not say a site's coordination because it saw only a file result's metadata).
CONTENT_CHARS = 4000
CONTENT_FILES = 3
CONTENT_FILE_BYTES = 256 * 1024
TABLE_ROWS = 30
_READABLE = ("json", "jsonl", "ndjson", "csv", "text/plain")


def _trimmed(value: Any) -> Any:
    """A value cut to a size a model can read: a table keeps its first rows, anything larger
    than the budget becomes a text preview."""
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        rows = cast(list[Any], value["rows"])
        if len(rows) > TABLE_ROWS:
            value = {**value, "rows": rows[:TABLE_ROWS], "rowsTotal": len(rows)}
    text = json.dumps(value, default=str)
    if len(text) <= CONTENT_CHARS:
        return value
    return {"preview": text[:CONTENT_CHARS], "truncated": True}


def _file_content(media_type: str, data: bytes) -> Any:
    text = data.decode("utf-8", errors="replace")
    if "jsonl" in media_type or "ndjson" in media_type:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return _trimmed({"rows": rows})
    if "json" in media_type:
        return _trimmed(json.loads(text))
    return _trimmed(text[: CONTENT_CHARS + 1])


def _content(room: RoomService, rec: ResultRecord) -> Any:
    """The readable content of a result: its value, or a preview of its small data files."""
    if rec.kind in ("json", "text", "category"):
        return _trimmed(rec.value)
    files: list[dict[str, Any]] = []
    for ref in rec.artifacts:
        if len(files) >= CONTENT_FILES:
            break
        if ref.size_bytes > CONTENT_FILE_BYTES or not any(k in ref.media_type for k in _READABLE):
            continue
        stored = room.store.artifact(ref.sha256)
        if stored is None:
            continue
        try:
            content = _file_content(ref.media_type, stored[1])
        except ValueError:
            continue
        files.append({"role": ref.role, "mediaType": ref.media_type, "content": content})
    return files or None


@op(
    "result.list",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {"case": _ID},
        "additionalProperties": False,
    },
    description="List results, stale ones included with the reason.",
    label="List results",
)
async def result_list(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    case = str(args.get("case") or "")
    rows: list[dict[str, Any]] = []
    for node in (ctx.room.read("results") or {}).values():
        rec = ResultRecord.model_validate(node)
        if not case or rec.case == case:
            rows.append(_result_row(rec))
    rows.sort(key=lambda r: (r["status"] != "current", r["id"]))
    return OpResult(
        summary=f"{len(rows)} results",
        data={"results": rows[:RESULT_ROWS], "more": max(0, len(rows) - RESULT_ROWS)},
        result_ids=tuple(r["id"] for r in rows[:RESULT_ROWS]),
    )


@op(
    "result.read",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {"id": _ID},
        "required": ["id"],
        "additionalProperties": False,
    },
    description=(
        "Read one result with its provenance, validity and content: its value, or a preview of "
        "its data files (tables keep their first rows). Answer questions about a result from "
        "this content."
    ),
    label="Read a result",
)
async def result_read(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    rec = ctx.room.result(str(args["id"]))
    data = _result_row(rec)
    content = _content(ctx.room, rec)
    if content is not None:
        data["content"] = content
    data["provenance"] = rec.provenance.model_dump(mode="json")
    data["validity"] = rec.validity.model_dump(mode="json")
    data["inputRevisions"] = rec.input_revisions
    ctx.turn.seen_results.add(rec.id)
    return OpResult(summary=f"read {rec.id}", data=data, result_ids=(rec.id,))


@op(
    "proposal.read",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {"id": _ID},
        "required": ["id"],
        "additionalProperties": False,
    },
    description="Read a proposal that involves you, with its reason (untrusted text).",
    label="Read a proposal",
)
async def proposal_read(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    prop: Proposal = ctx.room.proposal(str(args["id"]))
    if ctx.person not in (prop.on_behalf_of, prop.lane_owner):
        raise Denied("that proposal does not involve you")
    return OpResult(
        summary=f"read proposal {prop.id}",
        data={
            "id": prop.id,
            "author": prop.author,
            "lane": f"{prop.case}/{prop.target_lane}",
            "owner": prop.lane_owner,
            "status": prop.status.value,
            "patch": [p.wire() for p in prop.patch],
            "evidence": prop.evidence,
            "reason": fence(prop.reason, source="proposal", id=prop.id),
        },
        taint=True,
    )


@op(
    "run.status",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {"id": _ID},
        "required": ["id"],
        "additionalProperties": False,
    },
    description="Status of a queued or running job, or of a run, by id.",
    label="Run status",
)
async def run_status(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    rid = str(args["id"])
    room = ctx.room
    job = (room.read("queue") or {}).get(rid)
    run = (room.read("runs") or {}).get(rid)
    if job is None and run is None:
        raise NotFound(f"no job or run {rid}")
    data: dict[str, Any] = {"id": rid}
    tainted = False
    if job is not None:
        data["job"] = {k: job.get(k) for k in ("kind", "case", "mode", "status", "requestedBy")}
    if run is not None:
        data["run"] = {k: run.get(k) for k in ("case", "tool", "tier", "status", "started")}
        if run.get("error"):
            data["run"]["error"] = fence(str(run["error"]), source="run-error", id=rid)
            tainted = True  # error text can come from a plugin
    return OpResult(summary=f"status of {rid}", data=data, taint=tainted)


@op(
    "lessons.recall",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 200},
            "scope": {
                "type": "string",
                "enum": ["room", "all"],
                "description": "room (default) or all, which adds other rooms and older lessons",
            },
        },
        "additionalProperties": False,
    },
    description=(
        "Recall this room's lessons (text written by others is untrusted). scope all also "
        "returns lessons from other rooms and older untagged ones, marked as such."
    ),
    label="Recall lessons",
)
async def lessons_recall(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    query = str(args.get("query") or "").lower()
    scope = "all" if args.get("scope") == "all" else "room"
    rows: list[dict[str, Any]] = []
    tainted = False
    for les, origin in ctx.room.lessons_with_origin(ctx.turn.aid, scope):
        if query and query not in les.text.lower():
            continue
        mine = les.taught_by == ctx.person and les.source_decision is None
        tainted = tainted or not mine
        row: dict[str, Any] = {
            "id": les.id,
            "scope": les.scope.value,
            "taughtBy": les.taught_by,
            "text": fence(les.text, source="lesson", id=les.id, cap=500),
        }
        if origin != ctx.room.id:  # another room's lesson, or an older one with no room
            row["crossRoom"] = True
            row["origin"] = origin or "legacy"
        rows.append(row)
    return OpResult(summary=f"{len(rows)} lessons", data={"lessons": rows[:20]}, taint=tainted)


@op(
    "chat.history",
    risk=RiskClass.R0,
    min_autonomy=Autonomy.OBSERVE,
    schema={
        "type": "object",
        "properties": {"query": {"type": "string", "maxLength": 200}},
        "additionalProperties": False,
    },
    description="Search your own earlier chat turns with this agent.",
    label="Search chat history",
)
async def chat_history(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    if ctx.turn.profile != "owner":
        raise Denied("chat history is for the owner's own turns")
    query = str(args.get("query") or "").lower()
    rows: list[dict[str, Any]] = []
    for t in ctx.room.store.chat_turns.last(ctx.room.id, ctx.person, 100):
        if t.role.value not in ("user", "agent") or t.msg_id == ctx.turn.msg_id:
            continue
        if query and query not in t.text.lower():
            continue
        rows.append({"role": t.role.value, "seq": t.seq, "text": t.text[:400]})
    return OpResult(summary=f"{len(rows)} earlier turns", data={"turns": rows[-10:]})


# --- R1: the person's lane, proposals and warnings -----------------------------------------


@op(
    "input.set",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "edits": {"type": "array", "items": _EDIT_SCHEMA, "minItems": 1, "maxItems": MAX_EDITS}
        },
        "required": ["edits"],
        "additionalProperties": False,
    },
    description=(
        "Set lane fields in one batch (at most ten). Numbers come from bindings: literal (a "
        "handle n1..nk from the person's words), result, derive, default, choice, preset."
    ),
    label="Edit inputs",
    prepare=_prepare_edits,
    card_kind=PendingActionKind.CONFIRM_EDIT,
)
async def input_set(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    """Commit the resolved edits: direct in lanes the person owns (autonomy permitting), a
    proposal to the owner otherwise."""
    turn, room = ctx.turn, ctx.room
    edits: list[dict[str, Any]] = args["edits"]
    reason: str = args["reason"]
    can_edit = at_least(turn.autonomy, Autonomy.EDIT_MY_LANES)
    direct: list[dict[str, Any]] = []
    proposed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in edits:
        key = (e["case"], e["lane"])
        if can_edit and _owner_of(room, *key) == ctx.person:
            direct.append(e)
        else:
            proposed.setdefault(key, []).append(e)

    def patch_of(items: list[dict[str, Any]]) -> list[PatchOp]:
        return [PatchOp(op="add", path=e["path"], value=e["value"]) for e in items]

    applied: list[str] = []
    if direct:
        try:
            room.edit_twin(turn.actor, patch_of(direct), reason, op_id=ctx.op_id)
            applied = [f"{e['case']}/{e['lane']}/{e['field']}" for e in direct]
        except LaneNotOwned:  # a lane moved to another owner meanwhile: propose instead
            for e in direct:
                proposed.setdefault((e["case"], e["lane"]), []).append(e)
    proposals: list[str] = []
    for (case, lane), items in proposed.items():
        pid = await send_proposal(
            turn, case, lane, patch_of(items), reason, [], f"{ctx.op_id}:{case}/{lane}"
        )
        proposals.append(pid)
    revisions = {
        f"{e['case']}/{e['lane']}": room.read("revisions", e["case"], e["lane"]) for e in edits
    }
    parts: list[str] = []
    if applied:
        parts.append(f"edited {len(applied)} field(s)")
    if proposals:
        parts.append(f"proposed to the lane owner: {', '.join(proposals)}")
    return OpResult(
        summary="; ".join(parts) or "no change",
        data={"applied": applied, "proposals": proposals, "revisions": revisions},
        wrote=True,
    )


def _prepare_proposal(turn: TurnState, args: dict[str, Any]) -> Prepared:
    case, lane = str(args["case"]), str(args["target_lane"])
    resolved, patch, reasons, revisions = resolve_edits(turn, list(args.get("edits") or []))
    if any((e["case"], e["lane"]) != (case, lane) for e in resolved):
        raise BindingError("one_lane", "a proposal targets exactly one lane; split it")
    results = _result_lookup(turn.room)
    evidence = [str(r) for r in args.get("evidence") or []]
    for rid in evidence:
        if results(rid) is None:
            raise BindingError("unknown_result", f"evidence {rid!r} is not a result")
    return Prepared(
        resolved={
            "case": case,
            "target_lane": lane,
            "edits": resolved,
            "reason": str(args["reason"]),
            "evidence": evidence,
        },
        reasons=reasons,
        display={"edits": _display_edits(resolved), "why": card_reason_words(reasons)},
        revisions=revisions,
        patch=patch,
    )


@op(
    "proposal.create",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "case": _ID,
            "target_lane": _ID,
            "edits": {"type": "array", "items": _EDIT_SCHEMA, "minItems": 1, "maxItems": MAX_EDITS},
            "reason": {"type": "string", "minLength": 1, "maxLength": 500, "x-prose": True},
            "evidence": {"type": "array", "items": _ID, "maxItems": 10},
        },
        "required": ["case", "target_lane", "edits", "reason"],
        "additionalProperties": False,
    },
    description="Propose edits to one lane to its owner, with a reason and result ids as evidence.",
    label="Propose a change",
    prepare=_prepare_proposal,
    card_kind=PendingActionKind.CONFIRM_EDIT,
)
async def proposal_create(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    patch = [PatchOp(op="add", path=e["path"], value=e["value"]) for e in args["edits"]]
    pid = await send_proposal(
        ctx.turn,
        args["case"],
        args["target_lane"],
        patch,
        args["reason"],
        list(args["evidence"]),
        ctx.op_id,
    )
    return OpResult(summary=f"proposed {pid} to the lane owner", data={"proposal": pid}, wrote=True)


def _prepare_warning(turn: TurnState, args: dict[str, Any]) -> Prepared:
    to = str(args["to"])
    if turn.room.read("room", "people", to) is None:
        raise BindingError("unknown_person", f"{to!r} has no seat in this room")
    results = _result_lookup(turn.room)
    evidence = [str(r) for r in args.get("evidence") or []]
    for rid in evidence:
        if results(rid) is None:
            raise BindingError("unknown_result", f"evidence {rid!r} is not a result")
    return Prepared(
        resolved={
            "to": to,
            "case": str(args["case"]),
            "lane": str(args["lane"]),
            "message": str(args["message"]),
            "evidence": evidence,
        },
        display={"to": to, "lane": f"{args['case']}/{args['lane']}"},
    )


@op(
    "warning.raise",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "to": _ID,
            "case": _ID,
            "lane": _ID,
            "message": {"type": "string", "minLength": 1, "maxLength": 500, "x-prose": True},
            "evidence": {"type": "array", "items": _ID, "maxItems": 10},
        },
        "required": ["to", "case", "lane", "message"],
        "additionalProperties": False,
    },
    description="Raise a warning to a person about a lane. It changes nothing and pauses nobody.",
    label="Raise a warning",
    prepare=_prepare_warning,
    card_kind=PendingActionKind.CONFIRM_EDIT,
)
async def warning_raise(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    flag = ctx.room.warn(
        ctx.turn.actor,
        args["to"],
        args["case"],
        args["lane"],
        args["message"],
        list(args["evidence"]),
    )
    return OpResult(
        summary=f"raised warning {flag.id} to {args['to']}",
        data={"warning": flag.id},
        wrote=True,
    )


_NOTE: dict[str, Any] = {"type": "string", "maxLength": 200, "x-prose": True}


@op(
    "run.cancel",
    risk=RiskClass.R1,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {"id": _ID},
        "required": ["id"],
        "additionalProperties": False,
    },
    description="Cancel one of your own jobs.",
    label="Cancel a job",
    card_kind=PendingActionKind.CONFIRM_EDIT,
)
async def run_cancel(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    from .runs import supervisor_of

    jid = str(args["id"])
    cancelled = await supervisor_of(ctx.turn.kernel).cancel(ctx.room, jid, ctx.person)
    return OpResult(
        summary=f"cancelled {jid}" if cancelled else f"{jid} was not running",
        data={"id": jid, "cancelled": cancelled},
        result_ids=(jid,),
        wrote=cancelled,
    )


# --- R2: runs ------------------------------------------------------------------------------


RUN_INPUTS_CAP = 12  # fields shown on a run card
RUN_INPUT_TEXT_CAP = 40


def _short(value: Any) -> str:
    """A lane value as a short display string: ``350 K`` for a number with a unit."""
    if isinstance(value, dict) and "v" in value:
        v = value["v"]
        num = f"{v:g}" if isinstance(v, int | float) and not isinstance(v, bool) else str(v)
        text = f"{num} {value.get('unit', '')}".strip()
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= RUN_INPUT_TEXT_CAP else text[: RUN_INPUT_TEXT_CAP - 1] + "…"


def _own_lane_inputs(room: RoomService, person: str, case: str) -> list[dict[str, str]]:
    """The person's own lane fields a run of the case reads, as display strings. Another
    person's lane is never read here (the briefing's rule: others' lanes give no values)."""
    out: list[dict[str, str]] = []
    owners = room.lane_owners().get(case, {})
    for lane in sorted(owners):
        if owners[lane] != person:
            continue
        value = room.read("twin", case, lane) or {}
        if not isinstance(value, dict):
            continue
        for name in sorted(value):
            if len(out) >= RUN_INPUTS_CAP:
                return out
            out.append(
                {
                    "lane": lane,
                    "field": str(name)[:RUN_INPUT_TEXT_CAP],
                    "value": _short(value[name]),
                }
            )
    return out


def _prepare_run(turn: TurnState, args: dict[str, Any]) -> Prepared:
    from consilith_contracts.extensions import optional_extension

    from ..domain.plugin_access import require_case, require_extension
    from ..domain.service import run_card_args

    room = turn.room
    case = str(args["capability"])
    if case not in room.case_definitions():
        raise BindingError("unknown_case", f"no capability {case!r} in this room")
    require_case(room, case)
    if optional_extension("workflows", case) is None:
        raise BindingError("not_runnable", f"{case} has no workflow to run")
    require_extension(room, "workflows", case, case=case)
    mode = str(args.get("mode") or optional_extension("default_modes", case) or "default")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", mode):
        raise BindingError("bad_mode", "a mode is a short plain name")
    return Prepared(
        resolved=run_card_args(case, mode),
        display={
            "capability": case,
            "mode": mode,
            "capabilityLabel": room.case_definitions()[case].label,
            "inputs": _own_lane_inputs(room, turn.person, case),
        },
        revisions=room.revisions(case),
        mode=mode,
    )


@op(
    "run.start",
    risk=RiskClass.R2,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "capability": _ID,
            "mode": {"type": "string", "maxLength": 64},
            "note": _NOTE,
        },
        "required": ["capability"],
        "additionalProperties": False,
    },
    description=(
        "Start a run of a capability in the background and return its job id. The capability "
        "is the case id in me.canRun.capability (not a plugin tool name) and the mode is one of "
        "me.canRun.modes. Below the run setting the person confirms it on a card first."
    ),
    label="Start a run",
    prepare=_prepare_run,
    card_kind=PendingActionKind.CONFIRM_RUN,
)
async def run_start(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    """Start the run as a job. The workflow never runs inside the chat request: the supervisor
    makes the job (the same id for the same call) and the person's card follows its progress."""
    from .runs import supervisor_of

    case, mode = str(args["case"]), str(args["mode"])
    jid, how = await supervisor_of(ctx.turn.kernel).begin(
        ctx.room, ctx.actor, case, mode, ctx.op_id
    )
    _remember_follow_up(ctx, jid, how)
    said = {
        "started": f"started job {jid} ({case}, {mode})",
        "retry": f"job {jid} was already started ({case}, {mode})",
        "duplicate": f"the same run is already in progress as job {jid}",
    }
    return OpResult(
        summary=said[how],
        data={"jobId": jid, "case": case, "mode": mode, "how": how},
        result_ids=(jid,),
        wrote=how == "started",
    )


def _remember_follow_up(ctx: OpContext, jid: str, how: str) -> None:
    """A run an agent started from its person's chat carries that request, so the agent can carry
    on when the run settles (``RunSupervisor.on_settled``). Only the owner's own chat turns, never
    a peer turn, and never a turn that was itself an automatic follow-up."""
    from .runs import AUTO_PREFIX

    turn = ctx.turn
    if how != "started" or turn.profile != "owner" or turn.origin != "owner":
        return
    row = ctx.room.store.chat_turns.get(ctx.room.id, turn.person, turn.msg_id)
    request = row.text if row is not None and row.role is TurnRole.USER else ""
    if not request:
        # A run approved on a card executes in the resume turn, which has no words of its own:
        # the request is the person's latest message in this room.
        users = [
            r
            for r in ctx.room.store.chat_turns.last(ctx.room.id, turn.person, 20)
            if r.role is TurnRole.USER and r.text
        ]
        request = users[-1].text if users else ""
    if not request or request.startswith(AUTO_PREFIX):
        return
    entry = dict(ctx.room.read("queue", jid) or {})
    if not entry:
        return
    entry["followUp"] = {"person": turn.person, "request": request[:600]}
    ctx.room.set_queue_entry(jid, entry)


# --- R3: lanes and lessons, confirmed by the person every time ------------------------------


def _prepare_lane(turn: TurnState, args: dict[str, Any]) -> Prepared:
    case, lane = str(args["case"]), str(args["lane"])
    cdef = turn.room.case_definitions().get(case)
    if cdef is None:
        raise BindingError("unknown_case", f"no case {case!r} in this room")
    if cdef.lane(lane) is None:
        raise BindingError("unknown_lane", f"case {case} has no lane {lane!r}")
    return Prepared(
        resolved={"case": case, "lane": lane},
        display={"lane": f"{case}/{lane}"},
        revisions={},
    )


_LANE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"case": _ID, "lane": _ID, "note": _NOTE},
    "required": ["case", "lane"],
    "additionalProperties": False,
}


@op(
    "lane.claim",
    risk=RiskClass.R3,
    min_autonomy=Autonomy.SUGGEST,
    schema=_LANE_SCHEMA,
    description="Ask to take a free lane for your person. They confirm it on a card.",
    label="Claim a lane",
    prepare=_prepare_lane,
    card_kind=PendingActionKind.CONFIRM_CLAIM,
)
async def lane_claim(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    case, lane = args["case"], args["lane"]
    ctx.room.claim_lane(ctx.person, case, lane, ctx.op_id, actor=ctx.turn.actor)
    return OpResult(
        summary=f"claimed lane {case}/{lane}", data={"lane": f"{case}/{lane}"}, wrote=True
    )


@op(
    "lane.release",
    risk=RiskClass.R3,
    min_autonomy=Autonomy.SUGGEST,
    schema=_LANE_SCHEMA,
    description="Ask to give up a lane your person owns. They confirm it on a card.",
    label="Release a lane",
    prepare=_prepare_lane,
    card_kind=PendingActionKind.CONFIRM_CLAIM,
)
async def lane_release(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    case, lane = args["case"], args["lane"]
    ctx.room.release_lane(ctx.person, case, lane, ctx.op_id, actor=ctx.turn.actor)
    return OpResult(
        summary=f"released lane {case}/{lane}", data={"lane": f"{case}/{lane}"}, wrote=True
    )


def _prepare_lesson(turn: TurnState, args: dict[str, Any]) -> Prepared:
    scope = str(args["scope"])
    resolved: dict[str, Any] = {"scope": scope, "text": str(args["text"])}
    if scope == "case":
        case, lane = args.get("case"), args.get("lane")
        if not case or not lane:
            raise BindingError("bad_arguments", "a case lesson names its case and lane")
        _lane = _prepare_lane(turn, {"case": case, "lane": lane})
        resolved.update(_lane.resolved)
    return Prepared(resolved=resolved, display={"scope": scope}, revisions={})


@op(
    "lesson.remember",
    risk=RiskClass.R3,
    min_autonomy=Autonomy.SUGGEST,
    schema={
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["personal", "room", "case"]},
            "text": {"type": "string", "minLength": 1, "maxLength": 200, "x-prose": True},
            "case": _ID,
            "lane": _ID,
        },
        "required": ["scope", "text"],
        "additionalProperties": False,
    },
    description=(
        "Keep a lesson for later turns: personal (only you), room, or case (a lane you own). "
        "Every lesson needs the person's confirmation on a card."
    ),
    label="Remember a lesson",
    prepare=_prepare_lesson,
    card_kind=PendingActionKind.CONFIRM_LESSON,
)
async def lesson_remember(ctx: OpContext, args: dict[str, Any]) -> OpResult:
    from consilith_contracts import MemoryScope

    scope, text = str(args["scope"]), str(args["text"])
    if scope == "personal":
        les = ctx.room.add_personal_note(ctx.person, text, actor=ctx.turn.actor, op_id=ctx.op_id)
    else:
        les = ctx.room.add_shared_lesson(
            ctx.person,
            MemoryScope.CASE if scope == "case" else MemoryScope.ROOM,
            text,
            args.get("case"),
            args.get("lane"),
            actor=ctx.turn.actor,
            op_id=ctx.op_id,
        )
    return OpResult(
        summary=f"kept a {scope} lesson ({les.id})",
        data={"lesson": les.id, "scope": scope},
        wrote=True,
    )
