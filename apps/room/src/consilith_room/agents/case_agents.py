"""Compatibility adapters for configured scientific proposal policies."""

from typing import Any

from consilith_contracts.extensions import all_extensions, extension

from .execution import RoomProposalContext


def __getattr__(name: str) -> Any:
    if name.startswith("_"):
        raise AttributeError(name)
    if name == "STRATEGIES":
        return {key: __getattr__(key) for key in all_extensions("strategies")}
    if name in all_extensions("strategies"):
        strategy = extension("strategies", name)

        async def run(room: Any, results: Any, propose: Any) -> Any:
            return await strategy(RoomProposalContext(room, name, propose), results)

        return run
    try:
        return extension("strategy_exports", name)
    except LookupError as exc:
        raise AttributeError(name) from exc
