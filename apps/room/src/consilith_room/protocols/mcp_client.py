"""The room's MCP client: the solver registry (FR-LOOP-4, FR-PR-5, NFR-6).

Every call has a deadline. Every result must be a valid ``ResultEnvelope`` (units, provenance,
solver version, wall time); anything else is refused before it can reach the room. Every call
and result is journaled (MCP) so Agents at work and Show wiring can name it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, cast

from consilith_contracts import ResultEnvelope, new_id
from consilith_contracts.execution import ToolCallResult as CallResult
from consilith_contracts.units import UnitError, parse
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT
from pydantic import ValidationError

from .journal import Journal


class SolverError(Exception):
    """A tool answered with an execution error (bad input, not installed, solver failure)."""

    def __init__(self, tool: str, message: str, *, not_installed: bool = False) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.message = message
        self.not_installed = not_installed


class SolverTimeout(SolverError):
    pass


class ContractViolation(SolverError):
    """The tool answered, but not with a valid result envelope. Its numbers are refused."""


@dataclass
class ToolInfo:
    name: str
    server: str
    url: str
    server_version: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict[str, Any])


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        group = cast(BaseExceptionGroup[BaseException], exc)
        return [leaf for inner in group.exceptions for leaf in _leaves(inner)]
    return [exc]


def _is_timeout(exc: BaseException) -> bool:
    return any(
        isinstance(e, TimeoutError) or (isinstance(e, MCPError) and e.code == REQUEST_TIMEOUT)
        for e in _leaves(exc)
    )


def _first(exc: BaseException) -> str:
    leaf = _leaves(exc)[0]
    return type(leaf).__name__


class SolverRegistry:
    """Maps tool names to the MCP servers that serve them, discovered with tools/list."""

    def __init__(self, servers: dict[str, str], journal: Journal | None = None) -> None:
        self.servers = servers
        self.journal = journal
        self.tools: dict[str, ToolInfo] = {}
        self.unreachable: dict[str, str] = {}

    async def discover(self, timeout_s: float = 5.0) -> dict[str, ToolInfo]:
        self.tools.clear()
        self.unreachable.clear()
        discovered: dict[str, ToolInfo] = {}
        for name, url in self.servers.items():
            try:
                async with asyncio.timeout(timeout_s):
                    async with Client(url) as client:
                        listed = await client.list_tools()
                        version = client.server_info.version if client.server_info else None
            except Exception as exc:
                self.unreachable[name] = type(exc).__name__
                continue
            for tool in listed.tools:
                if tool.name in discovered:
                    raise ContractViolation(
                        tool.name, "ambiguous tool name across registered servers"
                    )
                discovered[tool.name] = ToolInfo(
                    name=tool.name,
                    server=name,
                    url=url,
                    server_version=version,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema or {}),
                )
        self.tools.update(discovered)
        return self.tools

    def has(self, tool: str) -> bool:
        return tool in self.tools

    async def call(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        deadline_s: float,
        room: str | None = None,
        caller: str = "conductor",
        correlation_id: str | None = None,
        progress: bool = False,
    ) -> CallResult:
        info = self.tools.get(tool)
        if info is None:
            raise SolverError(tool, "no reachable MCP server serves this tool", not_installed=True)
        call_id = correlation_id or new_id("call")
        if self.journal is not None and room is not None:
            self.journal.record(
                room,
                "MCP",
                "tools/call",
                caller,
                f"mcp:{info.server}",
                {"tool": tool, "arguments": arguments, "deadlineS": deadline_s},
                correlation_id=call_id,
            )
        started = time.perf_counter()
        try:
            async with asyncio.timeout(deadline_s):
                # The client's own read timeout is a backstop just past the deadline; if it
                # still fires first, it is the same timeout (handled below).
                async with Client(info.url, read_timeout_seconds=deadline_s + 1.0) as client:
                    current_version = client.server_info.version if client.server_info else None
                    if info.server_version and current_version != info.server_version:
                        raise ContractViolation(
                            tool,
                            "registered server implementation changed; rediscover before execution",
                        )
                    result = await client.call_tool(
                        tool,
                        arguments,
                        progress_callback=self._progress(room, caller, info, call_id)
                        if progress
                        else None,
                    )
        except Exception as exc:  # the MCP client raises timeouts and transport errors in groups
            if _is_timeout(exc):
                self._journal_result(room, caller, info, call_id, {"error": "timeout"})
                raise SolverTimeout(tool, f"no answer within {deadline_s:.1f} s") from exc
            self._journal_result(room, caller, info, call_id, {"error": "unreachable"})
            raise SolverError(tool, f"the {info.server} server failed: {_first(exc)}") from exc
        wall = time.perf_counter() - started
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content).strip() or "tool error"
            self._journal_result(room, caller, info, call_id, {"error": text, "wallS": wall})
            raise SolverError(tool, text, not_installed="not installed" in text.lower())
        try:
            env = ResultEnvelope.model_validate(result.structured_content or {})
            for q in env.quantities:
                parse(q.unit)
        except (ValidationError, UnitError) as exc:
            self._journal_result(room, caller, info, call_id, {"error": "contract violation"})
            raise ContractViolation(tool, f"result is not a valid envelope: {exc}") from exc
        summary = {
            "quantities": {q.name: [q.value, q.unit] for q in env.quantities},
            "outputs": [{"name": o.name, "kind": o.kind} for o in env.outputs],
            "tier": int(env.tier),
            "wallTimeS": env.provenance.wall_time_s,
            "validity": env.validity.wire(),
        }
        self._journal_result(room, caller, info, call_id, {"summary": summary, "wallS": wall})
        return CallResult(envelope=env, call_id=call_id, wall_s=wall)

    def _progress(self, room: str | None, caller: str, info: ToolInfo, call_id: str) -> Any:
        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            if self.journal is not None and room is not None:
                self.journal.record(
                    room,
                    "MCP",
                    "progress",
                    f"mcp:{info.server}",
                    caller,
                    {"tool": info.name, "progress": progress, "total": total, "message": message},
                    correlation_id=call_id,
                )

        return on_progress

    def _journal_result(
        self, room: str | None, caller: str, info: ToolInfo, call_id: str, payload: dict[str, Any]
    ) -> None:
        if self.journal is not None and room is not None:
            self.journal.record(
                room,
                "MCP",
                "tools/result",
                f"mcp:{info.server}",
                caller,
                {"tool": info.name, **payload},
                correlation_id=call_id,
            )
