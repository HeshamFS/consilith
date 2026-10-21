"""Compatibility facade for separately packaged scientific candidate exploration."""

from typing import Any

from consilith_contracts.execution import PluginExecutionError
from consilith_contracts.extensions import extension

from ..protocols.mcp_client import SolverError


async def explore(registry: Any, room_id: str, *args: Any, **kwargs: Any) -> Any:
    async def call(tool: str, arguments: dict[str, Any], **options: Any) -> Any:
        try:
            return await registry.call(tool, arguments, room=room_id, **options)
        except SolverError as exc:
            raise PluginExecutionError(exc.message) from exc

    return await extension("swarm_exports", "explore")(call, *args, **kwargs)


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    try:
        return extension("swarm_exports", name)
    except LookupError as exc:
        raise AttributeError(name) from exc
