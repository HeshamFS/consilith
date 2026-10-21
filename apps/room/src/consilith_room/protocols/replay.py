"""Exact replay of a recorded AG-UI session (NFR-8), for retakes.

The protocol journal holds every AG-UI event each viewer received, in order. A replay serves
one recorded run again, event by event, with ``metadata.replay = true`` on every event. It
writes nothing, calls no solver and changes no state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from ag_ui.core import Event
from ag_ui.encoder import EventEncoder
from pydantic import TypeAdapter

from .journal import Journal

EVENTS: TypeAdapter[Any] = TypeAdapter(Event)


def recorded_run(
    journal: Journal, room: str, viewer: str, session: str, run_id: str | None
) -> list[dict[str, Any]]:
    """The events of one recorded run, grouped by run id (runs may interleave), and only a
    complete one: it starts with RUN_STARTED and ends with RUN_FINISHED or RUN_ERROR."""
    runs: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for e in journal.entries(room):
        refs = e.get("refs", {})
        if e["protocol"] != "AG-UI" or e.get("viewer") != viewer or refs.get("session") != session:
            continue
        rid = refs.get("runId")
        if e.get("replay") or not rid:
            continue
        if rid not in runs:
            runs[rid] = []
            order.append(rid)
        runs[rid].append(e["payload"])
    complete = [
        r
        for r in order
        if runs[r]
        and runs[r][0].get("type") == "RUN_STARTED"
        and runs[r][-1].get("type") in ("RUN_FINISHED", "RUN_ERROR")
    ]
    if run_id is not None:
        complete = [r for r in complete if r == run_id]
    return runs[complete[-1]] if complete else []


async def replay_stream(events: list[dict[str, Any]], speed: float = 1.0) -> AsyncIterator[str]:
    encoder = EventEncoder()
    for data in events:
        labelled = {**data, "metadata": {**(data.get("metadata") or {}), "replay": True}}
        event = EVENTS.validate_python(labelled)
        yield encoder.encode(event)
        if speed > 0:
            await asyncio.sleep(0)  # cooperative; replays are not time-scaled here
