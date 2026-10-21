"""Bring your own agent (BRD "Agent lifecycle", SPEC "A2A").

A person pastes an A2A Agent Card URL. The room fetches and checks the card through the
outbound policy, and the brought agent replaces the spawned one with its owner's permissions.
It never receives a token: the room sends it room events and chat as A2A messages, and applies
what it returns (an ``AgentOps`` data part) through the same write gate as that agent, so the
drawer's autonomy and tool rules bind it exactly like a hosted agent.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from a2a.client import A2ACardResolver
from a2a.types import AgentCard
from consilith_contracts import EVENT_MEDIA_TYPE, AgentOps
from pydantic import ValidationError

from ..domain.errors import Denied, RoomError
from ..domain.service import RoomService
from ..protocols.a2a_client import A2ACaller
from ..protocols.journal import Journal
from ..protocols.outbound import CARD_SUFFIX, OutboundPolicy, OutboundRefused

log = logging.getLogger("consilith.brought")
ApplyOps = Callable[[RoomService, str, AgentOps], Awaitable[list[str]]]


class BroughtAgents:
    def __init__(self, policy: OutboundPolicy, journal: Journal) -> None:
        self.policy = policy
        self.caller = A2ACaller(
            journal, local_base="http://local.invalid", client_factory=lambda _b: policy.client()
        )
        self.cards: dict[tuple[str, str], AgentCard] = {}

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

    async def notify(
        self, room: RoomService, aid: str, event: dict[str, Any], apply: ApplyOps
    ) -> list[str]:
        """Send a room event; apply the ops the agent answers with, as that agent."""
        base, card = self._base(room, aid)
        try:
            reply = await self.caller.send(
                room.id,
                sender="conductor",
                receiver=aid,
                base=base,
                card=card,
                data=event,
                media_type=EVENT_MEDIA_TYPE,
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
            except RoomError as exc:
                room.note(
                    room_agent(), "agent.brought.refused", {"agent": aid, "reason": exc.message}
                )
                notes.append(f"refused: {exc.message}")
        return notes

    async def chat(self, room: RoomService, aid: str, text: str) -> str:
        base, card = self._base(room, aid)
        reply = await self.caller.send(
            room.id,
            sender=str(room.agent(aid)["owner"]),
            receiver=aid,
            base=base,
            card=card,
            text=text,
        )
        return " ".join(reply.texts) or "(no answer)"


def room_agent() -> Any:
    from ..domain.actors import Actor

    return Actor.room_agent("conductor")
