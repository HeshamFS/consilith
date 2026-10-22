"""Bring your own agent (BRD "Agent lifecycle", SPEC "A2A").

A person pastes an A2A Agent Card URL. The room fetches and checks the card through the
outbound policy, and the brought agent replaces the spawned one with its owner's permissions.
It never receives a token: the room sends it room events and chat as A2A messages, and applies
what it returns (an ``AgentOps`` data part) through the same write gate as that agent, so the
drawer's autonomy and tool rules bind it exactly like a hosted agent.

Every message carries an ``AgentBrief`` (the server-built briefing, a digest of the tool
catalog, ``asOfSeq`` and the outcome of the agent's previous ops). The ``ops`` and ``surface`` it
answers with run through ``ToolHost`` under the profile of the message it answers: ``owner`` for
the owner's chat, ``event`` for a room event, ``peer`` for a peer envelope.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from a2a.client import A2ACardResolver
from a2a.types import AgentCard
from consilith_contracts import (
    EVENT_MEDIA_TYPE,
    PEER_MEDIA_TYPE,
    AgentOpCall,
    AgentOps,
    CatalogEntry,
    PeerEnvelope,
    ToolKind,
    new_id,
)
from pydantic import ValidationError

from ..domain.errors import Denied, RoomError
from ..domain.service import RoomService
from ..protocols.a2a_client import A2ACaller
from ..protocols.journal import Journal
from ..protocols.outbound import CARD_SUFFIX, OutboundPolicy, OutboundRefused
from .briefing import Audience, build_briefing
from .catalog import ToolCatalog, get_catalog
from .toolhost import ToolHost, ToolProfile
from .turn_events import ToolResult

log = logging.getLogger("consilith.brought")
ApplyOps = Callable[[RoomService, str, AgentOps], Awaitable[list[str]]]

# The A2A data part that carries the AgentBrief on chat messages and peer envelopes. A room
# event keeps its own media type and carries the brief under ``brief``.
BRIEF_MEDIA_TYPE = "application/vnd.consilith.brief+json"
BRIEF_KEY = "brief"

# What each profile may ask for. ``owner`` has no list: the registry, the agent's autonomy and
# the confirm cards decide. The lists are by op id, so the rule holds for ops that land later.
EVENT_OPS = frozenset({"proposal.create", "warning.raise", "ui.show"})
EVENT_NEVER = frozenset({"run.start", "peer.send", "lane.claim", "lesson.remember"})
PEER_OPS = frozenset({"peer.reply", "proposal.create"})
PROFILE_OPS: dict[str, frozenset[str] | None] = {
    "owner": None,
    "event": EVENT_OPS,
    "peer": PEER_OPS,
}
MAX_OPS = {"owner": 10, "event": 8, "peer": 2}

_OP_KINDS = frozenset({ToolKind.PLATFORM, ToolKind.UI, ToolKind.PEER})
EVENT_TURNS_PER_HOUR = 30
HOUR_S = 3600.0
LAST_OPS_KEPT = 12
REASON_CHARS = 200
# Keys that would carry an address or a server version; none of them reaches the brief.
_NEVER_KEYS = frozenset(
    {"url", "uri", "address", "host", "endpoint", "server", "server_version", "serverVersion"}
)


def _clean(value: Any) -> Any:
    """A copy of ``value`` without any key that could carry a server address or version."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in _NEVER_KEYS}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list):
        return [_clean(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value  # pyright: ignore[reportUnknownVariableType]


def profile_verdict(profile: str, op: str) -> str | None:
    """Why ``op`` may not run in ``profile``, decided by op id alone; ``None`` when the profile
    does not rule it out (the registry and the gate still decide)."""
    if profile == "event" and op in EVENT_NEVER:
        return f"{op} is never allowed from a reply to a room event"
    allowed = PROFILE_OPS.get(profile)
    if allowed is not None and op not in allowed:
        return f"{op} is not allowed in the {profile} profile"
    return None


class BroughtAgents:
    def __init__(
        self,
        policy: OutboundPolicy,
        journal: Journal,
        *,
        catalog: ToolCatalog | None = None,
        briefing: Callable[[RoomService, str, Audience], dict[str, Any]] = build_briefing,
        toolhost: ToolHost | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.policy = policy
        self.caller = A2ACaller(
            journal, local_base="http://local.invalid", client_factory=lambda _b: policy.client()
        )
        self.cards: dict[tuple[str, str], AgentCard] = {}
        self.catalog = catalog
        self.briefing = briefing
        self.toolhost = toolhost or ToolHost()
        self.kernel: Any = None  # the Kernel (``send_proposal``), set with ``bind``
        self._clock = clock
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._last_ops: dict[tuple[str, str], deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=LAST_OPS_KEPT)
        )

    def bind(self, kernel: Any) -> None:
        """Give the ops the kernel, so a proposal goes through the conductor."""
        self.kernel = kernel

    async def fetch(self, card_url: str) -> AgentCard:
        self.policy.check_url(card_url, card=True)
        base = card_url[: -len(CARD_SUFFIX)]
        async with self.policy.client() as http:
            card = await A2ACardResolver(http, base, CARD_SUFFIX).get_agent_card()
        rpc = [i for i in card.supported_interfaces if i.protocol_binding == "JSONRPC"]
        if not rpc or rpc[0].protocol_version != "1.0":
            raise Denied("the card must offer a JSONRPC interface for A2A 1.0")
        for iface in card.supported_interfaces:  # every endpoint it names, not only the card
            self.policy.check_url(iface.url)
            parts = urlsplit(iface.url)
            self.policy.resolve(
                parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80)
            )
        return card

    async def bring(self, room: RoomService, person: str, card_url: str) -> dict[str, Any]:
        try:
            card = await self.fetch(card_url)
        except OutboundRefused as exc:
            raise Denied(f"agent card refused: {exc}") from exc
        summary = {
            "name": card.name,
            "description": card.description,
            "version": card.version,
            "skills": [s.id for s in card.skills],
            "interfaces": [
                {"binding": i.protocol_binding, "version": i.protocol_version, "url": i.url}
                for i in card.supported_interfaces
            ],
        }
        room.set_brought_agent(person, card_url, summary)
        self.cards[(room.id, f"{person}.agent")] = card
        return summary

    def _base(self, room: RoomService, aid: str) -> tuple[str, AgentCard | None]:
        node = room.agent(aid)
        url = str(node.get("cardUrl") or "")
        return url[: -len(CARD_SUFFIX)], self.cards.get((room.id, aid))

    # the brief -------------------------------------------------------------------------------
    def _owner(self, room: RoomService, aid: str) -> str:
        return str(room.agent(aid)["owner"])

    def agent_brief(self, room: RoomService, aid: str, audience: Audience) -> dict[str, Any]:
        """The ``AgentBrief`` pushed with a message: the briefing for ``audience``, the catalog
        digest, ``asOfSeq`` and the outcome of this agent's previous ops. It holds no plugin tool
        schema, no server address and no ``server_version``."""
        owner = self._owner(room, aid)
        catalog = self.catalog or get_catalog()
        briefing = _clean(self.briefing(room, owner, audience))
        allowed = PROFILE_OPS[audience]
        entries = [
            e
            for e in catalog.for_person(room, owner, audience)
            if allowed is None or e.spec.ref in allowed
        ]
        modes: dict[str, set[str]] = defaultdict(set)
        for entry in entries:
            if entry.spec.kind is ToolKind.PLUGIN:
                for via in entry.reachable_via:
                    modes[via.capability].add(via.mode)
        return {
            "kind": "agent.brief",
            "audience": audience,
            "asOfSeq": briefing.get("asOfSeq", room.seq),
            "briefing": briefing,
            "catalog": {
                "digest": catalog.digest(catalog.render_block(entries)),
                "ops": [_op_line(e) for e in entries if e.spec.kind in _OP_KINDS],
                "plugins": [
                    {"capability": c, "modes": sorted(m)} for c, m in sorted(modes.items())
                ],
            },
            "lastOps": list(self._last_ops[(room.id, aid)]),
        }

    def _try_brief(self, room: RoomService, aid: str, audience: Audience) -> dict[str, Any] | None:
        try:
            return self.agent_brief(room, aid, audience)
        except Exception as exc:  # a brief that cannot be built never stops the message
            log.warning("brief for %s not built: %s", aid, type(exc).__name__)
            return None

    # event budget ----------------------------------------------------------------------------
    def event_turns_left(self, room_id: str, aid: str) -> int:
        queue = self._events[(room_id, aid)]
        now = self._clock()
        while queue and now - queue[0] >= HOUR_S:
            queue.popleft()
        return max(0, EVENT_TURNS_PER_HOUR - len(queue))

    def _spend_event_turn(self, room_id: str, aid: str) -> bool:
        if self.event_turns_left(room_id, aid) <= 0:
            return False
        self._events[(room_id, aid)].append(self._clock())
        return True

    # ops -------------------------------------------------------------------------------------
    def _record(self, room_id: str, aid: str, op: str, status: str, reason: str) -> dict[str, Any]:
        row = {"op": op, "status": status, "reason": " ".join(reason.split())[:REASON_CHARS]}
        self._last_ops[(room_id, aid)].append(row)
        return row

    def _briefing_for_turn(self, room: RoomService, person: str, audience: str) -> dict[str, Any]:
        aud: Audience = (
            "peer" if audience == "peer" else "event" if audience == "event" else "owner"
        )
        return self.briefing(room, person, aud)

    async def run_ops(
        self,
        room: RoomService,
        aid: str,
        ops: AgentOps,
        profile: ToolProfile,
        *,
        msg_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Run the ``ops`` and ``surface`` of one reply through ``ToolHost`` in ``profile``.

        Which ops a profile may call is decided here by op id first, so a ``run.start`` from an
        event reply is denied whether or not that op exists yet. Each outcome is kept for the next
        brief's ``lastOps``."""
        calls = list(ops.ops)
        if ops.surface is not None:
            plan = ops.surface.model_dump(mode="json", by_alias=True, exclude_none=True)
            calls.append(AgentOpCall(op="ui.show", args=plan))
        if not calls:
            return []
        turn = self.toolhost.fresh_turn(
            room, self._owner(room, aid), msg_id or new_id("brt"), profile
        )
        turn.kernel = self.kernel
        turn.taint = profile == "peer"  # peer text is class B; the brief and events carry none
        turn.origin = "event" if profile == "event" else "owner"
        turn.briefing = self._briefing_for_turn
        alias_of = {e.spec.ref: alias for alias, e in turn.entries.items()}
        out: list[dict[str, Any]] = []
        held = False
        for i, call in enumerate(calls):

            def add(status: str, reason: str, op: str = call.op) -> None:
                out.append(self._record(room.id, aid, op, status, reason))

            verdict = profile_verdict(profile, call.op)
            if verdict is not None:
                add("denied", verdict)
            elif i >= MAX_OPS[profile]:
                add("denied", f"at most {MAX_OPS[profile]} ops in one {profile} reply")
            elif held:
                add("error", "not executed: an earlier op needs the owner's confirmation")
            elif call.op not in alias_of:
                add("denied", f"{call.op} is not available to this agent in the {profile} profile")
            else:
                outcome = await self.toolhost.run_call_full(
                    turn, {"tool": alias_of[call.op], "args": call.args}, call_id=f"1.{i}"
                )
                add(*_status_of(outcome.result))
                held = outcome.approval is not None
        return out

    async def _run_blocks(
        self, room: RoomService, aid: str, blocks: list[dict[str, Any]], profile: ToolProfile
    ) -> tuple[list[dict[str, Any]], str | None]:
        outcomes: list[dict[str, Any]] = []
        message: str | None = None
        for block in blocks:
            try:
                ops = AgentOps.model_validate(block)
            except ValidationError:
                continue
            message = message or ops.message
            outcomes += await self.run_ops(room, aid, ops, profile)
        return outcomes, message

    # messages --------------------------------------------------------------------------------
    async def notify(
        self, room: RoomService, aid: str, event: dict[str, Any], apply: ApplyOps
    ) -> list[str]:
        """Send a room event with the brief; apply what the agent answers with, as that agent.

        The reply runs in the ``event`` profile. Each agent has an event budget of 30 turns an
        hour, so a run can never keep a chain of notifies going."""
        if self.kernel is None:
            self.kernel = getattr(apply, "__self__", None)
        if not self._spend_event_turn(room.id, aid):
            room.note(
                room_agent(),
                "agent.brought.budget",
                {"agent": aid, "perHour": EVENT_TURNS_PER_HOUR},
            )
            return []
        base, card = self._base(room, aid)
        owner = self._owner(room, aid)
        data = dict(event)
        brief = self._try_brief(room, aid, "event")
        if brief is not None:
            data[BRIEF_KEY] = brief
        try:
            reply = await self.caller.send(
                room.id,
                sender="conductor",
                receiver=aid,
                base=base,
                card=card,
                data=data,
                media_type=EVENT_MEDIA_TYPE,
                viewer=owner if brief is not None else None,  # the brief is the owner's own
            )
        except (
            OutboundRefused,
            Exception,
        ) as exc:  # a remote agent's failure never breaks the room
            log.warning("brought agent %s did not answer: %s", aid, type(exc).__name__)
            room.note(
                room_agent(),
                "agent.brought.unreachable",
                {"agent": aid, "error": type(exc).__name__},
            )
            return []
        notes: list[str] = []
        for block in reply.data:
            try:
                ops = AgentOps.model_validate(block)
            except ValidationError:
                continue
            try:
                notes += await apply(room, aid, ops)
                done = await self.run_ops(room, aid, ops, "event")
            except RoomError as exc:
                room.note(
                    room_agent(), "agent.brought.refused", {"agent": aid, "reason": exc.message}
                )
                notes.append(f"refused: {exc.message}")
                continue
            notes += [f"{o['op']}: {o['status']}" for o in done]
        return notes

    async def chat(self, room: RoomService, aid: str, text: str) -> str:
        """The owner's chat message with the brief. The reply's ops run in the ``owner`` profile."""
        base, card = self._base(room, aid)
        owner = self._owner(room, aid)
        brief = self._try_brief(room, aid, "owner")
        reply = await self.caller.send(
            room.id,
            sender=owner,
            receiver=aid,
            base=base,
            card=card,
            text=text,
            data=brief,
            media_type=BRIEF_MEDIA_TYPE if brief is not None else None,
            viewer=owner,  # a person's chat with their own agent is theirs alone
        )
        _, message = await self._run_blocks(room, aid, reply.data, "owner")
        return " ".join(reply.texts) or message or "(no answer)"

    async def deliver_peer(
        self,
        room: RoomService,
        aid: str,
        envelope: PeerEnvelope,
        *,
        external_acknowledged: bool,
    ) -> PeerDelivery:
        """Hand a peer envelope to a brought agent. The text leaves the deployment here, so it goes
        only through the outbound policy, and only when the sender's first-contact card said this
        recipient is external. The reply runs in the ``peer`` profile, tainted."""
        if not room.agent(aid).get("brought"):
            raise Denied(f"{aid} is not a brought agent")
        if not external_acknowledged:
            raise Denied(
                "this agent runs outside the deployment; its first-contact card must say so first"
            )
        base, card = self._base(room, aid)
        owner = self._owner(room, aid)
        data = envelope.model_dump(mode="json", by_alias=True)
        brief = self._try_brief(room, aid, "peer")
        if brief is not None:
            data[BRIEF_KEY] = brief
        reply = await self.caller.send(
            room.id,
            sender="conductor",
            receiver=aid,
            base=base,
            card=card,
            data=data,
            media_type=PEER_MEDIA_TYPE,
            viewer=owner,
        )
        outcomes, message = await self._run_blocks(room, aid, reply.data, "peer")
        return PeerDelivery(texts=list(reply.texts), message=message, ops=outcomes)


class PeerDelivery:
    """What a brought agent answered to a peer envelope, and how its ops fared."""

    def __init__(self, texts: list[str], message: str | None, ops: list[dict[str, Any]]) -> None:
        self.texts = texts
        self.message = message
        self.ops = ops


def _op_line(entry: CatalogEntry) -> dict[str, Any]:
    spec = entry.spec
    line: dict[str, Any] = {
        "id": spec.ref,
        "risk": spec.risk.value,
        "minAutonomy": spec.min_autonomy.value,
        "availability": entry.availability.value,
        "schema": spec.input_schema,
    }
    if entry.reason:
        line["reason"] = entry.reason
    return line


_DENIED_CODES = frozenset(
    {"tool_blocked", "autonomy", "not_in_profile", "unknown_tool", "not_available"}
)


def _status_of(result: ToolResult) -> tuple[str, str]:
    if result.status == "ok":
        return "ok", result.summary
    if result.status == "awaiting_approval":
        return "awaiting_approval", "the owner must confirm this on a card"
    if result.code == "ruled_out":
        return "denied", "a lesson rules this change out"
    return ("denied" if result.code in _DENIED_CODES else "error"), result.summary


def room_agent() -> Any:
    from ..domain.actors import Actor

    return Actor.room_agent("conductor")
