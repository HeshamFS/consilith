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

    @router.get("/rooms/{room_id}/plugins/updates")
    def updates(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        """Whether the room's pinned releases differ from the installed ones (version or
        package digest), and the room owner, so the page offers the update to the owner only."""
        hub.refresh_plugins()
        room = hub.room(room_id)
        return {"changed": room.plugin_updates(p), "owner": hub.store.room_owner(room_id)}

    @router.post("/rooms/{room_id}/plugins/migrate")
    def migrate(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        """Only the room owner moves a room to newly installed plugin releases."""
        hub.refresh_plugins()
        if hub.store.room_owner(room_id) != p:
            raise Denied("only the room owner updates the room's plugins", code="not_room_owner")
        room = hub.room(room_id)
        return {"changed": room.migrate_plugins(p), "room": room.snapshot()}

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
