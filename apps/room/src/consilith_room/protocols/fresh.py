"""Run one MCP tool call in a fresh solver process (FR-RG-7: a manifest replays in a fresh
process, so no cache or state from earlier runs can make a value look reproducible).

The solver's app factory is started with uvicorn on a free loopback port, asked once through
the same validating registry call as every other tool call (journaled like them), and stopped.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time

from consilith_contracts.execution import ToolCallResult as CallResult

from .journal import Journal
from .mcp_client import SolverError, SolverRegistry

START_TIMEOUT_S = 90.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def call_in_fresh_process(
    app_ref: str,
    server: str,
    tool: str,
    arguments: dict[str, object],
    *,
    deadline_s: float,
    journal: Journal | None,
    room: str,
    correlation_id: str,
    expected_server_version: str | None = None,
) -> CallResult:
    port = _free_port()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            app_ref,
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        registry = SolverRegistry({server: f"http://127.0.0.1:{port}/mcp"}, journal)
        started = time.monotonic()
        while tool not in registry.tools:
            if proc.poll() is not None:
                raise SolverError(tool, f"the fresh {server} process exited ({proc.returncode})")
            if time.monotonic() - started > START_TIMEOUT_S:
                raise SolverError(tool, f"the fresh {server} process did not start")
            await registry.discover(timeout_s=2.0)
            if tool not in registry.tools:
                await asyncio.sleep(0.3)
        if (
            expected_server_version is not None
            and registry.tools[tool].server_version != expected_server_version
        ):
            raise SolverError(tool, "the historical solver implementation identity differs")
        return await registry.call(
            tool,
            dict(arguments),
            deadline_s=deadline_s,
            room=room,
            caller="conductor",
            correlation_id=correlation_id,
        )
    finally:
        proc.terminate()
        try:
            await asyncio.to_thread(proc.wait, 10)  # never block the event loop
        except subprocess.TimeoutExpired:
            proc.kill()


__all__ = ["call_in_fresh_process"]
