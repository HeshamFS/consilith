"""Wires the room's agents: solver registry, A2A caller, conductor, hosted agents, follow-ups."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from consilith_contracts import (
    PEER_MEDIA_TYPE,
    LedgerEntry,
    PeerEnvelope,
    PeerSendRequest,
    PendingActionKind,
    Proposal,
)
from consilith_contracts.extensions import registry_operation
from consilith_contracts.review import DECISION_MEDIA_TYPE
from consilith_contracts.signing import load_public, verify_subject

from ..domain.errors import RoomError
from ..domain.service import RoomService
from ..platform.keys import SessionKeys
from ..platform.settings import Settings, loop_enabled, peers_enabled
from ..platform.store import Store
from ..platform.tokens import TokenIssuer
from ..protocols.a2a_client import A2ACaller
from ..protocols.journal import Journal
from ..protocols.mcp_client import SolverRegistry
from ..protocols.outbound import OutboundPolicy
from . import literature  # noqa: F401  (registers literature.search for every agent)
from .brought import BroughtAgents
from .catalog import ToolCatalog
from .composer import Composer
from .conductor import Conductor
from .hosted import ROOM_SYSTEM, HostedAgents, Reply
from .kernel import Kernel
from .loop import TurnRunnerImpl
from .meter import Meter
from .model_resolver import ModelResolver, meter_for
from .peer import PeerAck, PeerRelay, PeerReply, install, message_id_for, register_ops
from .prompt import PromptBuilder
from .queue import Tier2Queue
from .turn_events import TurnRunner

log = logging.getLogger("consilith.runtime")
FollowUp = Callable[[RoomService, Proposal, bool], Awaitable[None]]


def pinned_verifier(path: Path | None) -> Callable[[LedgerEntry], bool]:
    """Verify with the referee key pinned in settings. No key pinned: refuse every entry."""
    if path is None or not path.exists():
        log.warning("no referee public key pinned: ledger entries will be refused")
        return lambda _entry: False
    public = load_public(path.read_bytes())
    return lambda entry: verify_subject(public, entry.subject, entry.signature)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        journal: Journal,
        tokens: TokenIssuer,
        rooms: Callable[[str], RoomService],
        local_app: Callable[[], Any],
        reply: Reply,
        keys: SessionKeys | None = None,
    ) -> None:
        self.settings = settings
        self.tokens = tokens
        # The agent loop's turn runner: set below when the loop flag is on and its gate is green.
        self.turn_runner: TurnRunner | None = None
        self.registry = SolverRegistry(
            settings.resolved_solver_urls,
            journal,
        )
        self.caller = A2ACaller(journal, local_base=settings.public_url, local_app=local_app)
        # A peer delivery waits for the recipient's model, which can take longer than the 120 s a
        # proposal call gets, so deliveries have their own caller.
        self.peer_caller = A2ACaller(
            journal, local_base=settings.public_url, local_app=local_app, timeout_s=210.0
        )
        self.hosted = HostedAgents(rooms, settings.public_url, self.registry, reply)
        self.conductor = Conductor(
            store,
            self.registry,
            self.caller,
            referee_base=settings.referee_url,
            referee_token=settings.referee_token or None,
            verify=pinned_verifier(settings.referee_public_key_path),
        )
        self.composer = Composer(journal)
        # One meter and one resolver for the whole process: the hourly cap and the one-call-at-a-
        # time rule hold only if every model call counts on the same meter.
        self.meter: Meter = meter_for(store)
        self.resolver = ModelResolver(keys, meter=self.meter)
        self.kernel = Kernel(self.conductor, self.composer, self.send_proposal, keys, self.resolver)
        self.brought = BroughtAgents(OutboundPolicy(settings.outbound_dev_allowlist), journal)
        self.kernel.brought = self.brought
        self.brought.bind(self.kernel)
        from . import models as _models

        _models.SIWC = _models.SiwcClient(
            settings.siwc_url, settings.siwc_secret, settings.data_dir / "siwc_owner"
        )
        self.hosted.explore = None
        self.extra_followups: list[FollowUp] = [self.kernel.after_decision]
        self.tier2 = Tier2Queue(
            store,
            rooms,
            self._tier2_solve,
            on_state_change=self._queue_surface,
        )
        self.kernel.tier2 = self.tier2
        # The agent loop, only when its flag is on and its security gate is green.
        if loop_enabled(settings):
            self.turn_runner = TurnRunnerImpl(
                keys=keys,
                kernel=self.kernel,
                catalog=ToolCatalog(self.registry),
                prompt=PromptBuilder(),
            )
            self.kernel.turn_runner = self.turn_runner
            # A run an agent started settles: the agent gets one automatic turn to carry on.
            from .runs import follow_up_text, supervisor_of

            runner = self.turn_runner

            async def follow_up(room: RoomService, entry: dict[str, Any]) -> None:
                wanted = entry["followUp"]
                text = follow_up_text(
                    str(entry.get("case", "")),
                    str(entry.get("status", "")),
                    str(wanted.get("request", "")),
                    entry.get("error"),
                )
                await runner.collect_text(room, str(wanted["person"]), text, sid=None)

            try:
                supervisor_of(self.kernel).on_settled = follow_up
            except RoomError:
                log.info("runs are not available; no follow-up turns")

        # Agent-to-agent messages, only when their flag is on and gate stage 2 is green. With the
        # flag off nothing below runs: no peer ops are registered and no hosted agent relays.
        self.peers: PeerRelay | None = None
        if peers_enabled(settings):
            register_ops()
            self.peers = PeerRelay(
                store=store,
                rooms=rooms,
                resolve=self.kernel.resolve,
                propose=self.send_proposal,
                submit=self._peer_submit,
                deliver=self._peer_deliver,
            )
            self.hosted.peers = self.peers
            install(self.peers)
            # Every settled run is told to the requester's open agent threads at once.
            relay = self.peers
            try:
                from .runs import supervisor_of as _sup

                _sup(self.kernel).on_status = lambda room, entry: relay.tell_run_status(room, entry)
            except RoomError:
                log.info("runs are not available; no run status in threads")
            toolhost = getattr(self.turn_runner, "toolhost", None)
            if toolhost is not None:
                toolhost.card_executors[PendingActionKind.CONFIRM_PEER] = self.peers.send_from_card

    def resume_peers(self) -> int:
        """At startup: requeue the deliveries a dead process had claimed, and start the worker.
        Returns how many were requeued (0 when peer messages are off)."""
        return 0 if self.peers is None else self.peers.resume()

    async def _peer_submit(
        self, room: RoomService, aid: str, req: PeerSendRequest, message_id: str
    ) -> PeerAck:
        """The sender's agent hands its message to the conductor over A2A, under its own agent
        token. The journal keeps ids only."""
        reply = await self.caller.send(
            room.id,
            sender=aid,
            receiver="conductor",
            base=self.hosted.base(room.id, "conductor"),
            data=req.wire(),
            media_type=PEER_MEDIA_TYPE,
            token=self.agent_token(room.id, aid),
            message_id=message_id,
            journal_payload=False,
            journal_summary={
                "msgId": message_id_for(room.id, aid, message_id),
                "kind": req.kind.value,
                "hop": 1,
            },
        )
        for item in reply.data:
            if "peerAck" in item:
                return PeerAck.parse(item["peerAck"])
        return PeerAck(False, "refused", reply.texts[0] if reply.texts else "no answer")

    async def _peer_deliver(
        self, room: RoomService, to_agent: str, env: PeerEnvelope
    ) -> PeerReply | None:
        """The conductor delivers an envelope to the recipient's agent, with the conductor token
        the allowlist admits. The recipient answers in a peer turn; the journal keeps ids only."""
        reply = await self.peer_caller.send(
            room.id,
            sender="conductor",
            receiver=to_agent,
            base=self.hosted.base(room.id, to_agent),
            data=env.model_dump(mode="json", by_alias=True, exclude_none=True),
            media_type=PEER_MEDIA_TYPE,
            token=self.tokens.issue("conductor", "agent", room.id),
            message_id=f"deliver:{env.id}",
            journal_payload=False,
            journal_summary={"msgId": env.id, "kind": env.kind.value, "hop": env.hop},
        )
        for item in reply.data:
            if "peerReply" in item:
                value = item["peerReply"]
                return None if value is None else PeerReply.parse(value)
        raise RoomError(reply.texts[0] if reply.texts else "no answer from the agent")

    def _queue_surface(self, room: RoomService, case: str) -> None:
        for viewer in self.composer.viewers(room):
            self.composer.publish(room, case, viewer, "live")

    @registry_operation
    async def _tier2_solve(self, room: RoomService, who: Any, case: str, mode: str) -> Any:
        from consilith_contracts.extensions import optional_extension

        self.conductor.authorize(room, who, case)
        handler = (optional_extension("deferred_workflows", case) or {}).get(mode)
        if handler is not None:
            from ..domain.plugin_access import require_extension

            require_extension(room, "deferred_workflows", case, case=case)
            return await handler()
        return await self.kernel.solve(room, who, case=case, mode=mode, tier=2)

    async def send_proposal(self, room: RoomService, aid: str, data: dict[str, Any]) -> str | None:
        """A personal agent proposes through the conductor over A2A (FR-AG-6)."""
        from consilith_contracts.review import PROPOSAL_MEDIA_TYPE

        reply = await self.caller.send(
            room.id,
            sender=aid,
            receiver="conductor",
            base=self.hosted.base(room.id, "conductor"),
            data=data,
            media_type=PROPOSAL_MEDIA_TYPE,
            token=self.agent_token(room.id, aid),
        )
        return reply.task_id

    def room_token(self, room: str) -> str:
        return self.tokens.issue(ROOM_SYSTEM, "agent", room)

    def agent_token(self, room: str, aid: str) -> str:
        return self.tokens.issue(aid, "agent", room)

    async def after_decision(self, room: RoomService, prop: Proposal, accept: bool) -> None:
        """Continue the proposal's A2A task to COMPLETED, then run any extra follow-ups
        (the kernel re-solves after an accepted change, FR-LOOP-8)."""
        current = room.proposal(prop.id)
        if current.a2a_task:
            decision = next(
                (
                    d
                    for d in (room.read("decisions") or {}).values()
                    if d.get("proposal") == prop.id
                ),
                {},
            )
            await self.caller.send(
                room.id,
                sender=ROOM_SYSTEM,
                receiver="conductor",
                base=self.hosted.base(room.id, "conductor"),
                data={"decision": decision},
                media_type=DECISION_MEDIA_TYPE,
                token=self.room_token(room.id),
                task_id=current.a2a_task,
            )
        for follow in self.extra_followups:
            await follow(room, current, accept)
