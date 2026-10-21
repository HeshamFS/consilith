"""Installed plugin discovery and explicit, seat-authorized room activation."""

from collections.abc import Callable
from typing import Annotated, Any

from consilith_contracts.extensions import (
    PackagePin,
    extension,
    package_for,
    plugin_descriptors,
    plugin_for,
    registry_scope,
)
from fastapi import APIRouter, Depends

from ..domain.errors import Conflict, Denied, NotFound
from .hub import Hub


def plugins_router(hub: Hub, person: Callable[..., str]) -> APIRouter:
    router = APIRouter()

    @router.get("/plugins")
    def available() -> dict[str, Any]:
        hub.refresh_plugins()
        return {"plugins": plugin_descriptors()}

    @router.post("/rooms/{room_id}/plugins/{plugin_id}/activate")
    def activate(
        room_id: str, plugin_id: str, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        hub.refresh_plugins()
        room = hub.room(room_id)
        try:
            room.activate_plugin(p, plugin_id)
        except LookupError as exc:
            raise NotFound("plugin is not installed") from exc
        return room.snapshot()

    @router.get("/rooms/{room_id}/knowledge/{knowledge_id}")
    def knowledge(
        room_id: str, knowledge_id: str, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        hub.refresh_plugins()
        with registry_scope():
            room = hub.room(room_id)
            if room.read("room", "people", p) is None:
                raise Denied("a room seat is required")
            owner = plugin_for("knowledge_documents", knowledge_id)
            if owner is None or owner["id"] not in (room.read("room", "activePlugins") or []):
                raise NotFound("knowledge resource is not active in this room")
            pin = package_for("knowledge_documents", knowledge_id)
            if pin is None or pin not in [
                PackagePin.model_validate(raw) for raw in room.read("packagePins") or []
            ]:
                raise Conflict("knowledge package differs from the version pinned by this room")
            document = extension("knowledge_documents", knowledge_id)
            if not isinstance(document, dict):
                raise NotFound("knowledge resource is unavailable")
            return {"id": knowledge_id, **document}

    return router
