"""The agent's tool catalog, and the operator gate for solver discovery (section 5)."""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ..agents.catalog import ToolCatalog, configure_catalog
from ..domain.errors import Denied, RoomError
from ..platform.settings import Settings
from .hub import Hub


class TooManyRequests(RoomError):
    status = 429
    code = "rate_limit"


@dataclass(frozen=True)
class Limit:
    """At most ``count`` calls per ``window_s`` seconds, per person and route."""

    count: int
    window_s: float


# Discovery opens a connection to every registered server; warming runs one call on each.
DISCOVER_LIMIT = Limit(count=6, window_s=60.0)
WARM_LIMIT = Limit(count=2, window_s=60.0)


def is_operator(settings: Settings, person: str) -> bool:
    """Operators are named in ``CONSILITH_OPERATORS``. With none named, production admits nobody;
    development and test leave the gate open so a local stack still works."""
    if settings.operators:
        return person in settings.operators
    return settings.env != "production"


def operator_gate(
    hub: Hub,
    person: str,
    route: str,
    limit: Limit,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Refuse anyone but an operator, then refuse a caller over the limit."""
    if not is_operator(hub.settings, person):
        raise Denied(f"only an operator may call /solvers/{route}")
    windows: dict[tuple[str, str], deque[float]] = hub.extras.setdefault("solver_rate", {})
    now = clock()
    window = windows.setdefault((route, person), deque())
    while window and now - window[0] >= limit.window_s:
        window.popleft()
    if len(window) >= limit.count:
        retry = max(1, round(limit.window_s - (now - window[0])))
        raise TooManyRequests(f"/solvers/{route} is rate limited; retry in {retry} s")
    window.append(now)


def catalog_router(hub: Hub, person: Callable[..., str]) -> APIRouter:
    router = APIRouter()
    catalog = ToolCatalog(hub.runtime.registry)
    configure_catalog(catalog)

    @router.get("/rooms/{room_id}/agent/tools")
    def tools(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        """Ids, descriptions, schemas and risk. Never a server address, URL or version."""
        hub.refresh_plugins()
        room = hub.room(room_id)
        if room.read("room", "people", p) is None:
            raise Denied(f"{p} has no seat in room {room_id}")
        entries = catalog.for_person(room, p)
        block = catalog.render_block(entries)
        return {"tools": catalog.agent_view(entries), "digest": catalog.digest(block)}

    return router
