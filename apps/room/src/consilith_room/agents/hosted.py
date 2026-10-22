"""The room's hosted A2A agents: cards and executors (FR-PR-6, FR-AG-4..6, FR-PR-2).

A two-person room has six agents: two personal agents, the conductor, the swarm, the solver
registry (all hosted here) and the referee (its own service). Personal agents never call solvers;
they delegate to the conductor. A proposal is an A2A task on the conductor that waits in
INPUT_REQUIRED until the lane owner decides; the room then continues it to COMPLETED.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from a2a.helpers import new_task_from_user_message
from a2a.helpers.proto_helpers import get_data_parts, get_text_parts, new_data_part
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    TaskState,
)
from a2a.utils.errors import TaskNotCancelableError
from consilith_contracts import (
    PEER_MEDIA_TYPE,
    PEER_REPLY_MEDIA_TYPE,
    AgentProposalOp,
    PatchOp,
    PeerEnvelope,
    PeerSendRequest,
)
from consilith_contracts.review import (
    DECISION_MEDIA_TYPE,
    PROPOSAL_MEDIA_TYPE,
    REGISTRY_MEDIA_TYPE,
    SWARM_MEDIA_TYPE,
)
from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from ..domain.actors import Actor
from ..domain.errors import RoomError
from ..domain.plugin_access import active_skills, active_tools
from ..domain.service import RoomService
from ..platform import skills as skill_store
from ..protocols.a2a_host import HostedAgent, caller_of
from ..protocols.mcp_client import SolverRegistry

if TYPE_CHECKING:
    from .peer import PeerRelay

log = logging.getLogger("consilith.hosted")
EXT_URI = "https://consilith.local/a2a/ext/agent-profile/v1"
VERSION = "0.1.0"
ROOM_AGENTS = ("conductor", "swarm", "solvers")
ROOM_SYSTEM = "room"  # the room server itself, when it continues a proposal task
# A request for a personal agent's detailed profile, answered only to an authenticated caller.
PROFILE_MEDIA_TYPE = "application/vnd.consilith.agent-profile+json"

Reply = Callable[[RoomService, str, str], Awaitable[str]]
SwarmFn = Callable[[RoomService, dict[str, Any]], Awaitable[dict[str, Any]]]


def _ext(params: dict[str, Any]) -> AgentExtension:
    st = struct_pb2.Struct()
    st.update(params)
    return AgentExtension(
        uri=EXT_URI, description="Consilith agent profile", required=False, params=st
    )


def _interface(base: str) -> list[AgentInterface]:
    return [
        AgentInterface(protocol_binding="JSONRPC", protocol_version="1.0", url=f"{base}/jsonrpc")
    ]


def agent_profile(room: RoomService, aid: str) -> dict[str, Any]:
    """The detailed profile of a personal agent: owner, lanes, tools, autonomy, permissions and
    a memory count. It is not on the unauthenticated card; an authenticated caller asks the
    agent for it (``PROFILE_MEDIA_TYPE``). It never names the provider or the model."""
    node = room.agent(aid)
    owner = str(node["owner"])
    cfg = room.agent_config(aid)
    _loaded, missing = skill_store.load(cfg.skills, active_skills(room))
    owned = [
        f"{c}/{ln}"
        for c, lanes in room.lane_owners().items()
        for ln, o in lanes.items()
        if o == owner
    ]
    lessons = room.lessons_for(aid)
    return {
        "owner": owner,
        "kind": "personal",
        "brought": bool(node.get("brought")),
        "lanes": owned,
        "tools": sorted(t for t in active_tools(room) if cfg.tool_allowed(t)),
        "toolsDenied": cfg.tools_denied,
        "autonomy": cfg.autonomy.value,
        "permissions": {"edits": owned, "proposesElsewhere": True},
        "computeBudgetS": cfg.compute_budget_s,
        # A memory summary is a count. Lesson texts, the goal and the watches stay with the
        # owner (the drawer and the authenticated config endpoint).
        "memory": {"lessons": len(lessons)},
        "missingSkills": missing,
        "status": node.get("status", "idle"),
    }


def personal_card(room: RoomService, aid: str, base: str) -> AgentCard:
    """The unauthenticated card of a personal agent: its name, protocol and skills, and nothing
    else. It carries no lanes, tools, autonomy, provider or model, and it does not stream. The
    detail is behind the agent token (``agent_profile``)."""
    node = room.agent(aid)
    owner = str(node["owner"])
    cfg = room.agent_config(aid)
    loaded, _missing = skill_store.load(cfg.skills, active_skills(room))
    person = room.read("room", "people", owner) or {}
    return AgentCard(
        name=aid,
        description=f"Personal agent of {person.get('name', owner)}.",
        version=VERSION,
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(id=s.name, name=s.name, description=s.description, tags=["skill"])
            for s in loaded
        ],
        supported_interfaces=_interface(base),
    )


ROOM_AGENT_TEXT = {
    "conductor": "Room agent owned by nobody. Recognises inputs, builds the twin, routes to a "
    "studio, composes each person's surfaces, holds proposals until their owner decides. "
    "Edits no lane.",
    "swarm": "Room agent owned by nobody. Explores a design space on the tier-0 model and returns "
    "candidates and a trade-off front. Proposes candidates only.",
    "solvers": "Room agent owned by nobody: the MCP solver registry. Lists the solver servers and "
    "their tools; answers tool calls only through the conductor.",
}
ROOM_AGENT_SKILLS = {
    "conductor": ["conductor-routing"],
    "swarm": ["swarm-explore"],
    "solvers": ["solver-registry"],
}


def room_agent_card(
    name: str, base: str, registry: SolverRegistry | None, room: RoomService | None = None
) -> AgentCard:
    loaded, _missing = skill_store.load(
        ROOM_AGENT_SKILLS[name], active_skills(room) if room else set()
    )
    profile: dict[str, Any] = {"owner": None, "kind": "room", "permissions": {"edits": []}}
    if name == "solvers" and registry is not None:
        # Tool names only: server addresses are internal and never on a public card.
        profile["tools"] = sorted(set(registry.tools) & (active_tools(room) if room else set()))
    return AgentCard(
        name=name,
        description=ROOM_AGENT_TEXT[name],
        version=VERSION,
        capabilities=AgentCapabilities(
            streaming=False, push_notifications=False, extensions=[_ext(profile)]
        ),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(id=s.name, name=s.name, description=s.description, tags=["skill"])
            for s in loaded
        ],
        supported_interfaces=_interface(base),
    )


def _message(
    text: str, data: dict[str, Any] | None = None, media_type: str | None = None
) -> Message:
    parts = [Part(text=text)]
    if data is not None:
        parts.append(new_data_part(data, media_type=media_type))
    return Message(role=Role.ROLE_AGENT, message_id=str(uuid.uuid4()), parts=parts)


def _media_data(msg: Message, media_type: str) -> list[dict[str, Any]]:
    """The data parts of a message that carry ``media_type``."""
    return [
        MessageToDict(p.data)
        for p in msg.parts
        if p.HasField("data") and p.media_type == media_type
    ]


class _Base(AgentExecutor):
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Only the room cancels a hosted task: a proposal must end by its owner's decision."""
        if caller_of(context) != ROOM_SYSTEM:  # also guarded in RoomRequestHandler
            raise TaskNotCancelableError(message="only the room may cancel this task")
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


class PersonalExecutor(_Base):
    """Talks for one person's agent. Only room agents may address it, and whatever they send runs
    as a peer turn: the owner's own words never arrive here (they come over AG-UI), so nothing
    that reaches this executor has the owner's authority."""

    def __init__(
        self,
        room: RoomService,
        aid: str,
        reply: Reply | None = None,
        peers: PeerRelay | None = None,
    ) -> None:
        self.room = room
        self.aid = aid
        self.reply = reply  # kept for callers that still pass it; no path here uses it
        self.peers = peers

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        caller = caller_of(context)
        msg = context.message
        if msg is None:
            return
        if caller is None:
            await event_queue.enqueue_event(
                _message("refused: an authenticated agent token is required")
            )
            return
        if _media_data(msg, PROFILE_MEDIA_TYPE):
            profile = agent_profile(self.room, self.aid)
            await event_queue.enqueue_event(
                _message("profile", {"profile": profile}, PROFILE_MEDIA_TYPE)
            )
            return
        if caller not in (ROOM_SYSTEM, *ROOM_AGENTS, "referee"):
            # Another person's agent never speaks to this agent directly: agents negotiate
            # through the conductor, and nobody gets an answer on this owner's behalf.
            await event_queue.enqueue_event(
                _message(f"refused: {caller} may not address {self.aid}; go through the conductor")
            )
            return
        peers = self.peers
        if peers is None:
            # Peer messages are off: nothing a room agent sends here is acted on, and nothing is
            # answered in the owner's name. The agent only acknowledges.
            await event_queue.enqueue_event(
                _message("noted: this agent takes no instructions from other agents")
            )
            return
        sent = _media_data(msg, PEER_MEDIA_TYPE)
        if sent and caller != "conductor":
            await event_queue.enqueue_event(
                _message("refused: only the conductor delivers a peer message")
            )
            return
        try:
            if sent:
                envelope = PeerEnvelope.model_validate(sent[0])
            else:
                text = " ".join(get_text_parts(list(msg.parts))) or "(data)"
                envelope = peers.envelope_for_text(self.room, self.aid, caller, text)
        except (ValidationError, RoomError):
            await event_queue.enqueue_event(_message("refused: not a valid peer message"))
            return
        if envelope.to.agent != self.aid:
            await event_queue.enqueue_event(_message("refused: that message is for another agent"))
            return
        reply = await peers.respond(self.room, self.aid, envelope)
        if reply is None:
            await event_queue.enqueue_event(
                _message("(no reply)", {"peerReply": None}, PEER_REPLY_MEDIA_TYPE)
            )
            return
        await event_queue.enqueue_event(
            _message(reply.text or "(no reply)", {"peerReply": reply.wire()}, PEER_REPLY_MEDIA_TYPE)
        )


class ConductorExecutor(_Base):
    def __init__(self, room: RoomService, peers: PeerRelay | None = None) -> None:
        self.room = room
        self.peers = peers

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        caller = caller_of(context)
        msg = context.message
        if msg is None:
            return
        data = get_data_parts(list(msg.parts))
        current = context.current_task
        if caller is None:
            await event_queue.enqueue_event(
                _message("refused: an authenticated agent token is required")
            )
            return
        if current is not None and current.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            await self._continue(context, event_queue, caller, data)
            return
        # The relay branch runs before the ".agent means proposal" branch: a peer message is a
        # data part with its own media type, and it is never read as a proposal.
        sent = _media_data(msg, PEER_MEDIA_TYPE)
        if sent:
            await self._relay(msg, event_queue, caller, sent[0])
            return
        if data and caller.endswith(".agent"):
            await self._propose(context, event_queue, caller, data[0])
            return
        await event_queue.enqueue_event(
            _message("conductor: send a proposal (" + PROPOSAL_MEDIA_TYPE + ") or a question")
        )

    async def _relay(self, msg: Message, q: EventQueue, caller: str, data: dict[str, Any]) -> None:
        """Relay one peer message. The sender is the token's agent (``caller_of``), never a field
        of the message, and it supplies only ``to``, ``kind``, ``text`` and ``refs``: the
        envelope is built by the conductor (``PeerRelay.accept``)."""

        async def refuse(code: str, text: str) -> None:
            ack = {"ok": False, "code": code, "text": text}
            await q.enqueue_event(_message(f"refused: {text}", {"peerAck": ack}))

        if self.peers is None:
            await refuse("off", "agent-to-agent messages are not switched on")
            return
        if not caller.endswith(".agent"):
            await refuse("refused", "only a personal agent sends a peer message")
            return
        try:
            request = PeerSendRequest.model_validate(data)
            node = self.room.agent(caller)
        except (ValidationError, RoomError):
            await refuse("refused", "not a valid peer message")
            return
        if node.get("owner") != caller.removesuffix(".agent"):
            await refuse("refused", "caller is not that person's agent")
            return
        ack = await self.peers.accept(self.room, caller, request, msg.message_id)
        await q.enqueue_event(
            _message("queued" if ack.ok else f"refused: {ack.text}", {"peerAck": ack.wire()})
        )

    async def _propose(
        self, context: RequestContext, q: EventQueue, caller: str, data: dict[str, Any]
    ) -> None:
        msg = context.message
        assert msg is not None
        task = context.current_task or new_task_from_user_message(msg)
        await q.enqueue_event(task)
        up = TaskUpdater(event_queue=q, task_id=task.id, context_id=task.context_id)
        await up.start_work(up.new_agent_message(parts=[Part(text="checking the proposal")]))
        try:
            op = AgentProposalOp.model_validate(data)
            owner = caller.removesuffix(".agent")
            node = self.room.agent(caller)
            if node.get("owner") != owner:
                raise RoomError("caller is not that person's agent")
            prop = self.room.propose(
                Actor.agent_of(owner),
                op.case,
                op.target_lane,
                [PatchOp.model_validate(p.wire()) for p in op.patch],
                op.reason,
                op.evidence,
                op_id=f"a2a:{task.id}",
            )
            self.room.set_proposal_task(prop.id, task.id)
        except (RoomError, ValidationError) as exc:
            reason = exc.message if isinstance(exc, RoomError) else "invalid proposal"
            await up.failed(up.new_agent_message(parts=[Part(text=f"proposal refused: {reason}")]))
            return
        part = new_data_part({"proposal": prop.wire()}, media_type=PROPOSAL_MEDIA_TYPE)
        await up.requires_input(
            up.new_agent_message(parts=[Part(text=f"waiting on {prop.lane_owner} to decide"), part])
        )

    async def _continue(
        self, context: RequestContext, q: EventQueue, caller: str, data: list[dict[str, Any]]
    ) -> None:
        task = context.current_task
        assert task is not None
        up = TaskUpdater(event_queue=q, task_id=task.id, context_id=task.context_id)
        if caller != ROOM_SYSTEM or not data:
            # Only the room continues a proposal task, after the owner decided over AG-UI.
            await up.requires_input(
                up.new_agent_message(parts=[Part(text="still waiting on the lane owner")])
            )
            return
        decision = data[0].get("decision", {})
        await up.start_work()
        await up.add_artifact(
            parts=[new_data_part({"decision": decision}, media_type=DECISION_MEDIA_TYPE)],
            name="decision",
            last_chunk=True,
        )
        await up.complete(
            up.new_agent_message(
                parts=[Part(text="accepted" if decision.get("accept") else "declined")]
            )
        )


class RegistryExecutor(_Base):
    def __init__(self, registry: SolverRegistry, room: RoomService) -> None:
        self.registry = registry
        self.room = room

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        if msg is None:
            return
        if caller_of(context) is None:
            await event_queue.enqueue_event(
                _message("refused: an authenticated agent token is required")
            )
            return
        task = context.current_task or new_task_from_user_message(msg)
        await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        listing = {
            "servers": sorted(
                {t.server for n, t in self.registry.tools.items() if n in active_tools(self.room)}
            ),
            "tools": {
                n: {"server": t.server, "description": t.description, "inputSchema": t.input_schema}
                for n, t in self.registry.tools.items()
                if n in active_tools(self.room)
            },
            "unreachable": [
                s
                for s in self.registry.unreachable
                if any(
                    s == server["id"]
                    for p in (self.room.read("room", "plugins") or [])
                    for server in p.get("mcpServers", [])
                )
            ],
        }
        await up.add_artifact(
            parts=[new_data_part(listing, media_type=REGISTRY_MEDIA_TYPE)],
            name="registry",
            last_chunk=True,
        )
        await up.complete()


class SwarmExecutor(_Base):
    def __init__(self, room: RoomService, explore: SwarmFn | None) -> None:
        self.room = room
        self.explore = explore

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        if msg is None:
            return
        if caller_of(context) is None:
            await event_queue.enqueue_event(
                _message("refused: an authenticated agent token is required")
            )
            return
        task = context.current_task or new_task_from_user_message(msg)
        await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        data = get_data_parts(list(msg.parts))
        if self.explore is None or not data:
            await up.failed(
                up.new_agent_message(parts=[Part(text="swarm: send a design-space request")])
            )
            return
        result = await self.explore(self.room, data[0])
        await up.add_artifact(
            parts=[new_data_part(result, media_type=SWARM_MEDIA_TYPE)],
            name="front",
            last_chunk=True,
        )
        await up.complete()


class HostedAgents:
    """Builds the hosted agent for a (room, agent id), or None if there is no such agent."""

    def __init__(
        self,
        rooms: Callable[[str], RoomService],
        public_url: str,
        registry: SolverRegistry,
        reply: Reply,
        explore: SwarmFn | None = None,
    ) -> None:
        self.rooms = rooms
        self.public_url = public_url.rstrip("/")
        self.registry = registry
        self.reply = reply
        self.explore = explore
        self.peers: PeerRelay | None = None  # set by the runtime when peer messages are on

    def base(self, room: str, aid: str) -> str:
        return f"{self.public_url}/a2a/{room}/{aid}"

    def __call__(self, room_id: str, aid: str) -> HostedAgent | None:
        try:
            room = self.rooms(room_id)
        except RoomError:
            return None
        base = self.base(room_id, aid)
        if aid in ROOM_AGENTS:
            executor: AgentExecutor
            if aid == "conductor":
                executor = ConductorExecutor(room, self.peers)
            elif aid == "swarm":
                executor = SwarmExecutor(room, self.explore)
            else:
                executor = RegistryExecutor(self.registry, room)
            return HostedAgent(
                card=lambda: room_agent_card(aid, base, self.registry, room), executor=executor
            )
        if room.read("room", "agents", aid) is None:
            return None
        return HostedAgent(
            card=lambda: personal_card(room, aid, base),
            executor=PersonalExecutor(room, aid, self.reply, self.peers),
        )
