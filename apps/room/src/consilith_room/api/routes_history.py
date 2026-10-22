"""Chat history for the owner (section 9), and peer threads and settings (section 7).

``GET /rooms/{id}/agent/history`` returns the signed-in person's own last turns from
``chat_turns``. Reads are keyed by the authenticated person only: there is no viewer parameter,
so nobody can read another person's chat.

Every seated person reads every peer thread (decision 4). A thread is closed by one of its two
people; peer settings are each person's own; only the room owner switches external agents.
"""

from collections.abc import Callable
from typing import Annotated, Any
from uuid import uuid4

from consilith_contracts.chat import ChatTurn
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..agents.peer import PeerRefused, active_relay, thread_wire, threads_wire
from ..domain.errors import Denied, RoomError
from .hub import Hub

HISTORY_TURNS = 50
# The parts of ``ChatTurn.meta`` the interface may show: attribution for the tool-card header,
# the display facts of tool and approval cards, and the surface a ui call showed. Nothing else
# leaves the server.
META_KEYS = (
    "provider",
    "model_used",
    "stop",
    "actions",
    "tool",
    "ids",
    "outcome",
    "card",
    "notice",
    "surface",
)


def turn_wire(turn: ChatTurn) -> dict[str, Any]:
    return {
        "id": turn.msg_id,
        "seq": turn.seq,
        "ts": turn.ts,
        "role": turn.role.value,
        "status": turn.status.value,
        "taint": turn.taint,
        "text": turn.text,
        "runId": turn.run_id,
        "meta": {k: turn.meta[k] for k in META_KEYS if k in turn.meta},
    }


class PeerSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepts: bool | None = None
    paused: bool | None = None
    lessons: bool | None = None
    confirmSends: bool | None = None
    muted: list[str] | None = None


class PeerAskIn(BaseModel):
    """A person writes to a colleague's agent from their own chat (Hesham, 2026-10-22)."""

    model_config = ConfigDict(extra="forbid")
    to: str
    text: str
    kind: str = "question"


class PeerExternalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: bool


def history_router(hub: Hub, person: Callable[..., str]) -> APIRouter:
    router = APIRouter()

    @router.get("/rooms/{room_id}/agent/history")
    def agent_history(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        room = hub.room(room_id)
        if room.read("room", "people", p) is None:
            raise Denied(f"{p} has no seat in room {room_id}")
        turns = hub.store.chat_turns.last(room_id, p, HISTORY_TURNS)
        return {"asOfSeq": room.seq, "turns": [turn_wire(t) for t in turns]}

    @router.get("/rooms/{room_id}/threads")
    def threads(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        return {**threads_wire(hub.room(room_id), p), "enabled": hub.runtime.peers is not None}

    @router.get("/rooms/{room_id}/threads/{thread_id}")
    def thread(room_id: str, thread_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        return thread_wire(hub.room(room_id), p, thread_id)

    @router.post("/rooms/{room_id}/threads/{thread_id}/close")
    def close_thread(
        room_id: str, thread_id: str, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        return {"thread": hub.room(room_id).close_peer_thread(thread_id, p).wire()}

    @router.post("/rooms/{room_id}/peers/settings")
    def peer_settings(
        room_id: str, body: PeerSettingsIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        room = hub.room(room_id)
        changes = body.model_dump(exclude_none=True)
        for other in changes.get("muted", []):
            if other == p:
                raise RoomError("you cannot mute yourself")
            if room.read("room", "people", other) is None:
                raise RoomError(f"{other} has no seat in room {room_id}")
        return {"prefs": room.set_peer_prefs(p, **changes)}

    @router.post("/rooms/{room_id}/peers/ask")
    async def peer_ask(
        room_id: str, body: PeerAskIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        """Send a person's own words to a colleague's agent, as their agent: the same screening,
        opt-in, mute, pause, budgets, caps and prose checks as ``peer.send``. Asking is the
        approval of the contact. A refusal comes back as the calm sentence, not an error."""
        room = hub.room(room_id)
        if room.read("room", "people", p) is None:
            raise Denied(f"{p} has no seat in room {room_id}")
        relay = active_relay()
        if relay is None:
            return {"sent": False, "code": "off", "text": "Agent-to-agent messages are off here."}
        sender = f"{p}.agent"
        try:
            info = relay.screen(room, sender, body.to)
        except PeerRefused as exc:
            return {"sent": False, "code": exc.refusal, "text": exc.message}
        room.allow_peer_contact(p, info.recipient)
        args = {"to": info.recipient, "kind": body.kind, "text": body.text}
        ack = await relay.send_from_owner(room, sender, args, f"ask:{uuid4().hex}")
        if not ack.ok:
            return {"sent": False, "code": ack.code, "text": ack.text}
        return {"sent": True, "thread": ack.thread, "msgId": ack.msg_id, "hop": ack.hop}

    @router.post("/rooms/{room_id}/peers/external")
    def peer_external(
        room_id: str, body: PeerExternalIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        if hub.store.room_owner(room_id) != p:
            raise Denied("only the room owner switches external agents", code="not_room_owner")
        room = hub.room(room_id)
        room.set_peer_external(p, body.allowed)
        return {"externalAllowed": room.peer_external_allowed()}

    return router
