"""Room-scoped plugin authority, independent of any scientific domain."""

from __future__ import annotations

from typing import Any, cast

from consilith_contracts.extensions import (
    PackagePin,
    installed_pins,
    plugin_for,
    registry_operation,
)
from consilith_contracts.plugins import ReplayPolicy

from .errors import Denied


def active_plugins(room: Any) -> list[dict[str, Any]]:
    return list(room.read("room", "plugins") or [])


def active_tools(room: Any) -> set[str]:
    return {tool for plugin in active_plugins(room) for tool in plugin.get("tools", [])}


def active_skills(room: Any) -> set[str]:
    return {
        str(skill["id"]) for plugin in active_plugins(room) for skill in plugin.get("skills", [])
    }


def room_pins(room: Any) -> list[PackagePin]:
    return [
        PackagePin.model_validate(p)
        for p in cast(list[dict[str, Any]], room.read("packagePins") or [])
    ]


def require_pins(room: Any) -> None:
    pinned = room_pins(room)
    if not pinned:
        raise Denied("room has no pinned plugin releases; explicit room migration is required")
    installed = {p.entry_point: p for p in installed_pins()}
    if any(installed.get(p.entry_point) != p for p in pinned):
        raise Denied("a pinned plugin release is unavailable; explicit room migration is required")


def require_case(room: Any, case: str) -> None:
    require_pins(room)
    if case not in room.case_definitions():
        raise Denied(f"capability {case!r} is not activated in this room")


@registry_operation
def require_extension(room: Any, kind: str, name: str, *, case: str | None = None) -> None:
    require_pins(room)
    plugin = plugin_for(kind, name)
    if plugin is None or plugin["id"] not in (room.read("room", "activePlugins") or []):
        raise Denied(f"extension {kind}:{name} is not activated in this room")
    if case is not None and plugin["id"] not in case_plugins(room, case):
        raise Denied(f"extension {kind}:{name} is outside the capability plugin dependencies")


def require_tool(room: Any, tool: str) -> None:
    require_pins(room)
    if tool not in active_tools(room):
        raise Denied(f"tool {tool!r} is not exposed by an active room plugin")


def replay_policy(room: Any, case: str, tool: str = "") -> ReplayPolicy:
    plugins = active_plugins(room)
    for plugin in plugins:
        if tool and tool in plugin.get("tools", []):
            return ReplayPolicy.model_validate(plugin.get("replayPolicy") or {})
    for plugin in plugins:
        if case in plugin.get("capabilities", []):
            return ReplayPolicy.model_validate(plugin.get("replayPolicy") or {})
    return ReplayPolicy()


def case_plugins(room: Any, case: str) -> set[str]:
    """Only the capability's owning plugin and explicitly activated dependency plugins."""
    plugins = {p["id"]: p for p in active_plugins(room)}
    selected: set[str] = set()

    def visit(pid: str) -> None:
        if pid in selected:
            return
        selected.add(pid)
        for dependency in plugins[pid].get("dependencies", []):
            if dependency in plugins:
                visit(dependency)

    for pid, plugin in plugins.items():
        if case in plugin.get("capabilities", []):
            visit(pid)
    return selected


def case_tools(room: Any, case: str) -> set[str]:
    selected = case_plugins(room, case)
    return {
        tool
        for plugin in active_plugins(room)
        if plugin["id"] in selected
        for tool in plugin.get("tools", [])
    }
