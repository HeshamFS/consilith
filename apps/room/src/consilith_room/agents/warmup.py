"""Warm the solvers before a session is recorded, so no cold start exceeds 2 s (NFR-LAT-2).

Each installed solver gets one small representative call. The first call in a solver process
pays its imports, mesher start-up and caches; after this, the first real call does not. The
calls are outside any room: nothing is stored and nothing is journaled.
"""

from __future__ import annotations

import time
from typing import Any

from consilith_contracts.extensions import all_extensions, extension

from ..protocols.mcp_client import SolverError, SolverRegistry

COLD_LIMIT_S = 2.0


async def warm(registry: SolverRegistry, deadline_s: float = 60.0) -> dict[str, Any]:
    if not registry.tools:
        await registry.discover()
    calls: dict[str, dict[str, Any]] = {}
    for tool, args in all_extensions("warm_calls").values():
        if tool not in registry.tools:
            calls[tool] = {"skipped": "not available"}
            continue
        started = time.perf_counter()
        try:
            await registry.call(tool, args, deadline_s=deadline_s, caller="warmup")
            calls[tool] = {"wallS": round(time.perf_counter() - started, 3)}
        except SolverError as exc:
            calls[tool] = {"error": exc.message}
    return {"calls": calls, "coldLimitS": COLD_LIMIT_S}


__all__ = ["COLD_LIMIT_S", "warm"]


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    if name == "WARM_CALLS":
        return list(all_extensions("warm_calls").values())
    try:
        return extension("warm_exports", name)
    except LookupError as exc:
        raise AttributeError(name) from exc
