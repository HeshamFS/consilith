"""The room's hosted A2A agents: cards and executors (FR-PR-6, FR-AG-4..6, FR-PR-2).

A two-person room has six agents: two personal agents, the conductor, the swarm, the solver
registry (all hosted here) and the referee (its own service). Personal agents never call solvers;
they delegate to the conductor. A proposal is an A2A task on the conductor that waits in
INPUT_REQUIRED until the lane owner decides; the room then continues it to COMPLETED.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

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
from consilith_contracts import AgentProposalOp, PatchOp
from consilith_contracts.review import (
    DECISION_MEDIA_TYPE,
    PROPOSAL_MEDIA_TYPE,
    REGISTRY_MEDIA_TYPE,
    SWARM_MEDIA_TYPE,
)
from google.protobuf import struct_pb2
from pydantic import ValidationError

from ..domain.actors import Actor
from ..domain.errors import RoomError
from ..domain.plugin_access import active_skills, active_tools
from ..domain.service import RoomService
from ..platform import skills as skill_store
from ..protocols.a2a_host import HostedAgent, caller_of
from ..protocols.mcp_client import SolverRegistry

log = logging.getLogger("consilith.hosted")
EXT_URI = "https://consilith.local/a2a/ext/agent-profile/v1"
VERSION = "0.1.0"
ROOM_AGENTS = ("conductor", "swarm", "solvers")
ROOM_SYSTEM = "room"  # the room server itself, when it continues a proposal task

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


def personal_card(room: RoomService, aid: str, base: str) -> AgentCard:
    node = room.agent(aid)
    owner = str(node["owner"])
    cfg = room.agent_config(aid)
    loaded, missing = skill_store.load(cfg.skills, active_skills(room))
    owned = [
        f"{c}/{ln}"
        for c, lanes in room.lane_owners().items()
        for ln, o in lanes.items()
        if o == owner
    ]
    lessons = room.lessons_for(aid)
    profile = {
        "owner": owner,
        "kind": "personal",
        "brought": bool(node.get("brought")),
        "lanes": owned,
        "tools": sorted(t for t in active_tools(room) if cfg.tool_allowed(t)),
        "toolsDenied": cfg.tools_denied,
        "autonomy": cfg.autonomy.value,
        "permissions": {"edits": owned, "proposesElsewhere": True},
        "computeBudgetS": cfg.compute_budget_s,
        # The card is public: a memory summary is a count. Lesson texts, the goal and the
        # watches stay with the owner (the drawer and the authenticated config endpoint).
        "memory": {"lessons": len(lessons)},
        "missingSkills": missing,
        "status": node.get("status", "idle"),
    }
    person = room.read("room", "people", owner) or {}
    return AgentCard(
        name=aid,
        description=f"Personal agent of {person.get('name', owner)} "
        f"({person.get('roleLabel', '')}). Edits its owner's lanes directly and only proposes "
        "everywhere else.",
        version=VERSION,
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=False, extensions=[_ext(profile)]
        ),
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
            streaming=True, push_notifications=False, extensions=[_ext(profile)]
        ),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(id=s.name, name=s.name, description=s.description, tags=["skill"])
            for s in loaded
        ],
        supported_interfaces=_interface(base),
    )


def _message(text: str) -> Message:
    import uuid

    return Message(role=Role.ROLE_AGENT, message_id=str(uuid.uuid4()), parts=[Part(text=text)])


class _Base(AgentExecutor):
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Only the room cancels a hosted task: a proposal must end by its owner's decision."""
        if caller_of(context) != ROOM_SYSTEM:  # also guarded in RoomRequestHandler
            raise TaskNotCancelableError(message="only the room may cancel this task")
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


class PersonalExecutor(_Base):
    """Talks for one person's agent. Only its owner's tokens or room agents may address it."""

    def __init__(self, room: RoomService, aid: str, reply: Reply) -> None:
        self.room = room
        self.aid = aid
        self.reply = reply

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
        if caller not in (ROOM_SYSTEM, *ROOM_AGENTS, "referee"):
            # Another person's agent never speaks to this agent directly: agents negotiate
            # through the conductor, and nobody gets an answer on this owner's behalf.
            await event_queue.enqueue_event(
                _message(f"refused: {caller} may not address {self.aid}; go through the conductor")
            )
            return
        text = " ".join(get_text_parts(list(msg.parts))) or "(data)"
        owner = str(self.room.agent(self.aid)["owner"])
        answer = await self.reply(self.room, owner, text)
        await event_queue.enqueue_event(_message(answer or "(no answer)"))


class ConductorExecutor(_Base):
    def __init__(self, room: RoomService) -> None:
        self.room = room

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
        if data and caller.endswith(".agent"):
            await self._propose(context, event_queue, caller, data[0])
            return
        await event_queue.enqueue_event(
            _message("conductor: send a proposal (" + PROPOSAL_MEDIA_TYPE + ") or a question")
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
                executor = ConductorExecutor(room)
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
            executor=PersonalExecutor(room, aid, self.reply),
        )
