"""Checks on a plugin manifest's tool metadata, ids and server declarations.

``toolMeta`` and ``runMeta`` sit beside ``tools``. They are hints for display and later phases and
authorise nothing. This module accepts them as a mapping or as the JSON text of one (some entry
point metadata arrives serialised), and accepts a server's tool entries as plain names or as
``{"name": ...}`` objects, so ``tools/staged_release.py`` and the room read the same names.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from pydantic import ValidationError

from .plugins import RunMeta, ToolMeta


class ManifestMetaError(ValueError):
    """A manifest declares tool metadata, ids or servers that cannot be trusted."""


def _mapping(raw: object, what: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ManifestMetaError(f"{what} is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise ManifestMetaError(f"{what} must be an object")
    return {str(k): v for k, v in cast(Mapping[object, Any], raw).items()}


def server_tool_names(server: Mapping[str, Any]) -> list[str]:
    """The tool names one declared MCP server lists, from string or object entries."""
    names: list[str] = []
    for entry in cast(list[object], server.get("tools") or []):
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, Mapping):
            name = str(cast(Mapping[str, Any], entry).get("name", ""))
        else:
            raise ManifestMetaError("a server tool entry is a name or an object with a name")
        if not name:
            raise ManifestMetaError("a server tool entry has no name")
        names.append(name)
    return names


def normalize_tool_meta(raw: object) -> dict[str, ToolMeta]:
    """``toolMeta`` as ``tool -> ToolMeta``. Unknown keys and bad tiers are refused."""
    out: dict[str, ToolMeta] = {}
    for tool, value in _mapping(raw, "toolMeta").items():
        try:
            out[tool] = ToolMeta.model_validate(_mapping(value, f"toolMeta[{tool!r}]"))
        except ValidationError as exc:
            raise ManifestMetaError(f"toolMeta[{tool!r}] is invalid: {_first(exc)}") from exc
    return out


def normalize_run_meta(raw: object) -> dict[str, dict[str, RunMeta]]:
    """``runMeta`` as ``capability -> mode -> RunMeta``."""
    out: dict[str, dict[str, RunMeta]] = {}
    for case, modes in _mapping(raw, "runMeta").items():
        out[case] = {}
        for mode, value in _mapping(modes, f"runMeta[{case!r}]").items():
            try:
                out[case][mode] = RunMeta.model_validate(
                    _mapping(value, f"runMeta[{case!r}][{mode!r}]")
                )
            except ValidationError as exc:
                raise ManifestMetaError(
                    f"runMeta[{case!r}][{mode!r}] is invalid: {_first(exc)}"
                ) from exc
    return out


def _first(exc: ValidationError) -> str:
    err = exc.errors()[0]
    return f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"


def check_ids_have_no_slash(manifest: Mapping[str, Any]) -> None:
    """Plugin and server ids sit inside ``plugin/<id>/<server>/<tool>``, so they hold no ``/``."""
    plugin_id = str(manifest.get("id", ""))
    if "/" in plugin_id:
        raise ManifestMetaError(f"plugin id {plugin_id!r} contains '/'")
    for server in cast(list[Mapping[str, Any]], manifest.get("mcpServers") or []):
        if "/" in str(server.get("id", "")):
            raise ManifestMetaError(f"server id {server.get('id')!r} of {plugin_id!r} contains '/'")


def check_manifest_meta(manifest: Mapping[str, Any], *, strict_names: bool = False) -> None:
    """Raise ``ManifestMetaError`` unless the manifest's ids, servers and metadata are coherent.

    - ids hold no ``/``;
    - a tool sits in exactly one of the plugin's own servers (when it declares any), and every
      server tool is one of the plugin's ``tools``;
    - ``toolMeta`` and ``runMeta`` parse in their string or object forms; with ``strict_names``
      ``toolMeta`` also names only the plugin's tools and ``runMeta`` only its capabilities. The
      names are off by default because the metadata is a hint and authorises nothing.
    """
    check_ids_have_no_slash(manifest)
    declared = [str(t) for t in cast(list[object], manifest.get("tools") or [])]
    seen: dict[str, str] = {}
    servers = cast(list[Mapping[str, Any]], manifest.get("mcpServers") or [])
    for server in servers:
        for name in server_tool_names(server):
            if name in seen:
                raise ManifestMetaError(
                    f"tool {name!r} is listed by servers {seen[name]!r} and {server.get('id')!r}"
                )
            seen[name] = str(server.get("id", ""))
            if name not in declared:
                raise ManifestMetaError(f"server tool {name!r} is not in the plugin's tools")
    if servers:
        for name in declared:
            if name not in seen:
                raise ManifestMetaError(f"tool {name!r} is in none of the plugin's servers")
    for tool in normalize_tool_meta(manifest.get("toolMeta")):
        if strict_names and tool not in declared:
            raise ManifestMetaError(f"toolMeta names {tool!r}, which the plugin does not declare")
    capabilities = {str(c) for c in cast(list[object], manifest.get("capabilities") or [])}
    for case in normalize_run_meta(manifest.get("runMeta")):
        if strict_names and case not in capabilities:
            raise ManifestMetaError(f"runMeta names {case!r}, which is not a plugin capability")
