"""The room's MCP client: the solver registry (FR-LOOP-4, FR-PR-5, NFR-6).

Every call has a deadline. Every result must be a valid ``ResultEnvelope`` (units, provenance,
solver version, wall time); anything else is refused before it can reach the room. Every call
and result is journaled (MCP) so Agents at work and Show wiring can name it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, cast

import httpx2
from consilith_contracts import ResultEnvelope, new_id
from consilith_contracts.execution import ToolCallResult as CallResult
from consilith_contracts.units import UnitError, parse
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_TIMEOUT, create_mcp_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT
from pydantic import ValidationError

from .journal import Journal

ProgressSink = Callable[[float, float | None, str | None], None]
# A caller (the tier-2 worker) installs a sink to see a call's progress without journaling it.
progress_sink: ContextVar[ProgressSink | None] = ContextVar("progress_sink", default=None)
PROGRESS_MIN_INTERVAL_S = 1.0
PROGRESS_MIN_STEP = 0.02


class ProgressThrottle:
    """Lets a progress stream through at most once per interval, or on a step of its range.

    A solver may report thousands of ticks. Each one used to become a journal row and a feed
    event, so a long run could fill the journal. The first tick and the last one always pass.
    """

    def __init__(
        self,
        min_interval_s: float = PROGRESS_MIN_INTERVAL_S,
        min_step: float = PROGRESS_MIN_STEP,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.min_step = min_step
        self._clock = clock
        self._last_at: float | None = None
        self._last_fraction = 0.0

    def allow(self, progress: float, total: float | None) -> bool:
        fraction = progress / total if total else 0.0
        final = bool(total) and progress >= cast(float, total)
        now = self._clock()
        if self._last_at is None or final:
            ok = True
        else:
            ok = (now - self._last_at) >= self.min_interval_s and (
                not total or abs(fraction - self._last_fraction) >= self.min_step
            )
        if ok:
            self._last_at = now
            self._last_fraction = fraction
        return ok


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
    # Kept from tools/list: the declared result shape and the server's hints (readOnlyHint, ...).
    # Hints only: nothing here authorises a call.
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None


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


MAX_LIST_PAGES = 50


async def _list_all_tools(client: Any) -> list[Any]:
    """Every tool a server lists, following ``next_cursor`` until it ends (or repeats)."""
    tools: list[Any] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(MAX_LIST_PAGES):
        page = await client.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.next_cursor
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
    return tools


def http_client_for(deadline_s: float) -> httpx2.AsyncClient:
    """The HTTP client for one tool call: connect and write as the SDK default, reads allowed
    for the whole deadline (plus a little), so a long run is cut by the deadline alone."""
    return create_mcp_http_client(
        timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=deadline_s + 5.0)
    )


RESCUE_INTERVAL_S = 10.0  # how often a missing tool may trigger a fresh discovery


class SolverRegistry:
    """Maps tool names to the MCP servers that serve them, discovered with tools/list.

    ``discover`` builds the whole map first and swaps it in at the end, so a failed or partial
    discovery never leaves the registry empty. A tool name served by two servers quarantines
    only the later server (the one the plugin does not own it for, when ownership is known).
    ``owners`` (tool -> the server ids its plugin declares) rejects a tool that a server lists
    but its plugin never gave to that server.
    """

    def __init__(self, servers: dict[str, str], journal: Journal | None = None) -> None:
        self.servers = servers
        self.journal = journal
        self.tools: dict[str, ToolInfo] = {}
        self.unreachable: dict[str, str] = {}
        # server -> why it was set aside (a clashing tool name); its tools are not served.
        self.quarantined: dict[str, str] = {}
        # tool -> the server whose listing of it was rejected (not an owning server).
        self.rejected: dict[str, str] = {}
        # tool -> the server ids its plugin declares; None means ownership is not enforced.
        self.owners: dict[str, set[str]] | None = None
        # Bumps whenever the discovered map, or the catalog behind it, changes.
        self.revision = 0
        self._generation = 0
        self._last_rescue = float("-inf")  # monotonic time of the last on-demand discovery

    def bump_revision(self) -> int:
        self.revision += 1
        return self.revision

    async def discover(self, timeout_s: float = 5.0) -> dict[str, ToolInfo]:
        self._generation += 1
        mine = self._generation
        owners = self.owners
        listings: dict[str, list[ToolInfo]] = {}
        unreachable: dict[str, str] = {}
        for name, url in dict(self.servers).items():
            try:
                async with asyncio.timeout(timeout_s):
                    async with Client(url) as client:
                        listed = await _list_all_tools(client)
                        version = client.server_info.version if client.server_info else None
            except Exception as exc:
                unreachable[name] = type(exc).__name__
                continue
            listings[name] = [
                ToolInfo(
                    name=tool.name,
                    server=name,
                    url=url,
                    server_version=version,
                    description=tool.description or "",
                    input_schema=dict(tool.input_schema or {}),
                    output_schema=dict(tool.output_schema) if tool.output_schema else None,
                    annotations=tool.annotations.model_dump(exclude_none=True)
                    if tool.annotations
                    else None,
                )
                for tool in listed
            ]
        tools, quarantined, rejected = self._resolve(listings, owners)
        if mine != self._generation:  # a newer discovery started; it owns the swap
            return self.tools
        changed = (
            tools != self.tools or quarantined != self.quarantined or rejected != self.rejected
        )
        self.tools.clear()
        self.tools.update(tools)
        self.unreachable.clear()
        self.unreachable.update(unreachable)
        self.quarantined.clear()
        self.quarantined.update(quarantined)
        self.rejected.clear()
        self.rejected.update(rejected)
        if changed:
            self.revision += 1
        return self.tools

    @staticmethod
    def _resolve(
        listings: dict[str, list[ToolInfo]], owners: dict[str, set[str]] | None
    ) -> tuple[dict[str, ToolInfo], dict[str, str], dict[str, str]]:
        rejected: dict[str, str] = {}
        kept: dict[str, list[ToolInfo]] = {}
        for server, infos in listings.items():
            kept[server] = []
            for info in infos:
                if owners is not None and info.name in owners and server not in owners[info.name]:
                    rejected[info.name] = server
                else:
                    kept[server].append(info)
        claims: dict[str, list[str]] = {}
        for server, infos in kept.items():
            for info in infos:
                claims.setdefault(info.name, []).append(server)
        quarantined: dict[str, str] = {}
        for tool, servers in claims.items():
            if len(set(servers)) < 2:
                continue
            owning = [s for s in servers if owners is not None and s in owners.get(tool, set())]
            keeper = owning[0] if len(owning) == 1 else servers[0]
            for server in servers:
                if server != keeper:
                    quarantined.setdefault(server, f"tool name {tool!r} is also served elsewhere")
        tools: dict[str, ToolInfo] = {}
        for server, infos in kept.items():
            if server in quarantined:
                continue
            for info in infos:
                tools[info.name] = info
        return tools, quarantined, rejected

    def has(self, tool: str) -> bool:
        return tool in self.tools

    async def _rediscover_for(self, tool: str) -> ToolInfo | None:
        """List the servers again when a tool is missing: a server still starting when the room
        last listed (a slow import, a container that came up later) would otherwise stay unknown
        until restart. At most once per ``RESCUE_INTERVAL_S``, so a truly absent tool is cheap."""
        now = time.monotonic()
        if now - self._last_rescue < RESCUE_INTERVAL_S:
            return None
        self._last_rescue = now
        await self.discover()
        return self.tools.get(tool)

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
        info = self.tools.get(tool) or await self._rediscover_for(tool)
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
        sink = progress_sink.get()
        try:
            async with asyncio.timeout(deadline_s):
                # The client's own read timeout is a backstop just past the deadline; if it
                # still fires first, it is the same timeout (handled below). The HTTP read
                # timeout follows the deadline too: the SDK's default (300 s) would cut a long
                # tier-2 run that streams its answer.
                async with (
                    http_client_for(deadline_s) as http,
                    Client(
                        streamable_http_client(info.url, http_client=http),
                        read_timeout_seconds=deadline_s + 1.0,
                    ) as client,
                ):
                    current_version = client.server_info.version if client.server_info else None
                    if info.server_version and current_version != info.server_version:
                        raise ContractViolation(
                            tool,
                            "registered server implementation changed; rediscover before execution",
                        )
                    result = await client.call_tool(
                        tool,
                        arguments,
                        progress_callback=self._progress(room, caller, info, call_id, sink)
                        if progress or sink is not None
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

    def _progress(
        self,
        room: str | None,
        caller: str,
        info: ToolInfo,
        call_id: str,
        sink: ProgressSink | None = None,
    ) -> Any:
        throttle = ProgressThrottle()

        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            if sink is not None:
                sink(progress, total, message)  # the sink throttles for itself
            if not throttle.allow(progress, total):
                return
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
