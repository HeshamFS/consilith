"""The agent's pending actions.

``GET /rooms/{id}/agent/actions`` lists the person's own open confirm cards (WS-L). A card is
private: the list is keyed by the signed-in person only. ``GET /rooms/{id}/agent/jobs`` lists the
person's own jobs and ``POST /rooms/{id}/agent/jobs/{job}/cancel`` cancels one of them. The history
endpoint is in ``routes_history.py`` (section 9).
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..agents.runs import supervisor_of
from ..agents.toolhost import approval_request
from ..domain.errors import Denied, RoomError
from .hub import Hub


class NotImplementedRoute(RoomError):
    """The endpoint is wired and behind a session, but its package has not landed yet."""

    status = 501
    code = "not_implemented"


def agent_router(hub: Hub, person: Callable[..., str]) -> APIRouter:
    router = APIRouter()

    @router.get("/rooms/{room_id}/agent/actions")
    def pending_actions(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        room = hub.room(room_id)
        if room.read("room", "people", p) is None:
            raise Denied(f"{p} has no seat in room {room_id}")
        rows: list[dict[str, Any]] = []
        for action in room.store.pending_actions.open_for(room.id, p):
            req = approval_request(action)
            rows.append(
                {
                    "id": action.id,
                    "kind": action.kind.value,
                    "msgId": action.msg_id,
                    "message": req.message,
                    "card": req.card,
                    "note": req.note,
                    "expires": req.expires,
                    "origin": action.origin,
                }
            )
        return {"actions": rows}

    @router.get("/rooms/{room_id}/agent/jobs")
    def my_jobs(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        room = hub.room(room_id)
        if room.read("room", "people", p) is None:
            raise Denied(f"{p} has no seat in room {room_id}")
        queue = room.read("queue") or {}
        return {"jobs": [e for e in queue.values() if e.get("requestedByPerson") == p]}

    @router.post("/rooms/{room_id}/agent/jobs/{job_id}/cancel")
    async def cancel_job(
        room_id: str, job_id: str, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        room = hub.room(room_id)
        if room.read("room", "people", p) is None:
            raise Denied(f"{p} has no seat in room {room_id}")
        cancelled = await supervisor_of(hub.runtime.kernel).cancel(room, job_id, p)
        return {"id": job_id, "cancelled": cancelled}

    return router
