"""Room-scoped plugin authority, independent of any scientific domain."""

from __future__ import annotations

from typing import Any, NamedTuple, cast

from consilith_contracts.extensions import (
    PackagePin,
    installed_pins,
    plugin_for,
    registry_operation,
)
from consilith_contracts.manifest_meta import ManifestMetaError, server_tool_names
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


class ToolOwner(NamedTuple):
    plugin: str
    server: str  # "" for a plugin that declares no MCP servers (legacy manifests)
    case: str  # the plugin's one capability; "" when it has none or several


def tool_index(room: Any) -> dict[str, ToolOwner]:
    """``tool -> (plugin, server, case)`` for the room's active plugins.

    A tool sits in exactly one of its plugin's own ``mcpServers``. A tool that two servers or two
    plugins claim, one that none of the plugin's servers lists, and any tool of a plugin or server
    whose id contains ``/`` are left out, so ``require_tool`` refuses them. A plugin that declares
    no servers (a legacy manifest) keeps its ``tools`` with the server left empty; with no server
    there is none to be wrong about, so a legacy claim neither blocks nor overrides a server's.
    """
    claims: dict[str, list[ToolOwner]] = {}
    for plugin in active_plugins(room):
        pid = str(plugin["id"])
        if "/" in pid:
            continue
        caps = [str(c) for c in plugin.get("capabilities", [])]
        case = caps[0] if len(caps) == 1 else ""
        servers = [s for s in plugin.get("mcpServers", []) if "/" not in str(s.get("id", ""))]
        if not plugin.get("mcpServers"):
            for tool in plugin.get("tools", []):
                claims.setdefault(str(tool), []).append(ToolOwner(pid, "", case))
            continue
        declared = {str(t) for t in plugin.get("tools", [])}
        for server in servers:
            try:
                names = server_tool_names(server)
            except ManifestMetaError:
                continue
            for tool in names:
                if tool in declared:
                    claims.setdefault(tool, []).append(ToolOwner(pid, str(server["id"]), case))
    index: dict[str, ToolOwner] = {}
    for tool, owners in claims.items():
        backed = [o for o in owners if o.server]
        if len(backed) == 1:
            index[tool] = backed[0]
        elif not backed:  # legacy plugins name no server, so there is no server to be wrong about
            index[tool] = owners[0]
    return index


def server_owners(descriptors: list[dict[str, Any]]) -> dict[str, set[str]]:
    """``tool -> the server ids its plugins declare it under``, over all installed plugins. The
    registry rejects a server's listing of a tool when the server is not among them."""
    owners: dict[str, set[str]] = {}
    for plugin in descriptors:
        for server in plugin.get("mcpServers", []):
            try:
                names = server_tool_names(server)
            except ManifestMetaError:
                continue
            for tool in names:
                owners.setdefault(tool, set()).add(str(server.get("id", "")))
    return owners


def require_tool(room: Any, tool: str, server: str | None = None) -> None:
    """The tool must belong to exactly one server of an active plugin. When the caller passes
    the server that actually answered, it must be that plugin's own server for the tool."""
    require_pins(room)
    owner = tool_index(room).get(tool)
    if owner is None:
        if tool in active_tools(room):
            raise Denied(f"tool {tool!r} is not served by exactly one of its plugin's own servers")
        raise Denied(f"tool {tool!r} is not exposed by an active room plugin")
    if server is not None and owner.server and server != owner.server:
        raise Denied(f"tool {tool!r} is served by {server!r}, not by its plugin's own server")


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
