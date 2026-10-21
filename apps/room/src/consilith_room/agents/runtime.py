"""Wires the room's agents: solver registry, A2A caller, conductor, hosted agents, follow-ups."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from consilith_contracts import LedgerEntry, Proposal
from consilith_contracts.extensions import registry_operation
from consilith_contracts.review import DECISION_MEDIA_TYPE
from consilith_contracts.signing import load_public, verify_subject

from ..domain.service import RoomService
from ..platform.keys import SessionKeys
from ..platform.settings import Settings
from ..platform.store import Store
from ..platform.tokens import TokenIssuer
from ..protocols.a2a_client import A2ACaller
from ..protocols.journal import Journal
from ..protocols.mcp_client import SolverRegistry
from ..protocols.outbound import OutboundPolicy
from .brought import BroughtAgents
from .composer import Composer
from .conductor import Conductor
from .hosted import ROOM_SYSTEM, HostedAgents, Reply
from .kernel import Kernel
from .queue import Tier2Queue

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
        self.registry = SolverRegistry(
            settings.resolved_solver_urls,
            journal,
        )
        self.caller = A2ACaller(journal, local_base=settings.public_url, local_app=local_app)
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
        self.kernel = Kernel(self.conductor, self.composer, self.send_proposal, keys)
        self.brought = BroughtAgents(OutboundPolicy(settings.outbound_dev_allowlist), journal)
        self.kernel.brought = self.brought
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
        )
        self.kernel.tier2 = self.tier2

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
