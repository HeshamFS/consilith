"""AG-UI 1.0 for Consilith: the room feed (never interrupts) and the shared event helpers.

Each viewer holds two sessions (SPEC "AG-UI"):
- the feed: STATE_SNAPSHOT, then STATE_DELTA per log entry, A2UI surfaces as ACTIVITY_SNAPSHOT,
  conductor tool calls mirrored as TOOL_CALL_*, the decision log as CUSTOM ``consilith.log``,
  presence as CUSTOM ``consilith.presence``; it ends ``cancelled`` on rotation;
- the agent session (agents/session.py): text with the personal agent and approval interrupts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from ag_ui.core import (
    PROTOCOL_VERSION,
    ActivitySnapshotEvent,
    BaseEvent,
    CustomEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedCancelledOutcome,
    RunFinishedEvent,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from ..domain.service import RoomService
from ..platform.bus import RoomBus
from ..platform.store import LogRow
from .journal import Journal

KEEPALIVE = ": keepalive\n\n"

# Which protocol a room log kind is shown under in the decision log and Show wiring.
KIND_PROTOCOL = {
    "twin.edit": "AG-UI",
    "lane.claim": "AG-UI",
    "lane.release": "AG-UI",
    "room.join": "AG-UI",
    "agent.config": "AG-UI",
    "agent.status": "AG-UI",
    "proposal.open": "A2A",
    "proposal.decide": "AG-UI",
    "proposal.task": "A2A",
    "warning.open": "A2A",
    "run.open": "MCP",
    "run.results": "MCP",
    "run.failed": "MCP",
    "ledger.entry": "A2A",
    "tension.open": "A2UI",
    "memory.lesson": "A2A",
}


class Emitter:
    """Encodes events and journals every one, so a viewer's stream can be replayed exactly."""

    def __init__(
        self,
        journal: Journal,
        room: str,
        viewer: str,
        session: str,
        replay: bool = False,
        run_id: str | None = None,
    ):
        self.encoder = EventEncoder()
        self.journal = journal
        self.room = room
        self.viewer = viewer
        self.session = session
        self.replay = replay
        self.run_id = run_id

    def __call__(self, event: BaseEvent) -> str:
        data = event.model_dump(mode="json", by_alias=True, exclude_none=True)
        if self.replay:
            data.setdefault("metadata", {})["replay"] = True
            event = type(event).model_validate(data)
        self.journal.record(
            self.room,
            "AG-UI",
            str(data.get("type")),
            "room" if self.session == "feed" else f"{self.viewer}.agent",
            self.viewer,
            data,
            viewer=self.viewer,
            refs={"session": self.session, **({"runId": self.run_id} if self.run_id else {})},
            replay=self.replay,
        )
        return self.encoder.encode(event)


def started(inp: RunAgentInput) -> RunStartedEvent:
    return RunStartedEvent(
        thread_id=inp.thread_id, run_id=inp.run_id, protocol_version=PROTOCOL_VERSION
    )


def run_error(message: str, code: str) -> RunErrorEvent:
    return RunErrorEvent(message=message, code=code)


def log_entry_value(row: LogRow) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "ts": row.ts,
        "kind": row.kind,
        "protocol": KIND_PROTOCOL.get(row.kind, "ROOM"),
        "actor": row.actor,
        "meta": row.meta,
    }


def row_events(row: LogRow) -> list[BaseEvent]:
    events: list[BaseEvent] = []
    if row.patch:
        events.append(
            StateDeltaEvent(
                delta=cast(Any, row.patch),
                metadata={
                    "seq": row.seq,
                    "kind": row.kind,
                    "committedAt": row.ts,
                    "author": row.actor,
                },
            )
        )
    events.append(CustomEvent(name="consilith.log", value=log_entry_value(row)))
    return events


def journal_events(entry: dict[str, Any], viewer: str) -> list[BaseEvent]:
    """Turn journaled A2UI, MCP and A2A messages into the viewer's feed events."""
    proto, kind, payload = entry["protocol"], entry["kind"], entry.get("payload", {})
    wiring = CustomEvent(
        name="consilith.wiring",
        value={
            "journalSeq": entry["seq"],
            "protocol": proto,
            "kind": kind,
            "sender": entry["sender"],
            "receiver": entry["receiver"],
            "refs": entry.get("refs", {}),
        },
    )
    if proto == "A2UI":
        if entry.get("viewer") != viewer:
            return []
        surface = entry.get("refs", {}).get("surfaceId", "surface")
        return [
            ActivitySnapshotEvent(
                message_id=f"a2ui-{surface}",
                activity_type="a2ui-surface",
                content={"surfaceId": surface, "messages": payload.get("messages", [])},
                replace=True,
                metadata={"journalSeq": entry["seq"]},
            ),
            wiring,
        ]
    if proto == "MCP" and kind == "tools/call":
        call_id = entry.get("correlationId") or f"call-{entry['seq']}"
        return [
            ToolCallStartEvent(tool_call_id=call_id, tool_call_name=str(payload.get("tool"))),
            ToolCallArgsEvent(tool_call_id=call_id, delta=json.dumps(payload.get("arguments", {}))),
            ToolCallEndEvent(tool_call_id=call_id),
            wiring,
        ]
    if proto == "MCP" and kind == "tools/result":
        call_id = entry.get("correlationId") or f"call-{entry['seq']}"
        summary = json.dumps(payload.get("summary", {}))
        return [
            ToolCallResultEvent(message_id=f"res-{call_id}", tool_call_id=call_id, content=summary),
            wiring,
        ]
    if proto == "MCP" and kind == "progress":  # e.g. MD steps while the solver runs
        return [
            CustomEvent(
                name="consilith.progress", value={**payload, "callId": entry.get("correlationId")}
            )
        ]
    if proto == "A2A":
        return [wiring]
    return []


async def feed_stream(
    room: RoomService,
    viewer: str,
    inp: RunAgentInput,
    *,
    bus: RoomBus,
    journal: Journal,
    rotate_s: float,
    keepalive_s: float,
    on_open: Callable[[], None] | None = None,
    alive: Callable[[], bool] | None = None,
    prepare: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """One feed run: snapshot, then the viewer's current surfaces, then everything after, until
    rotation or disconnect. A client replaces its whole view with the snapshot and the surfaces,
    so a connection never depends on events it missed. ``prepare`` runs first: it makes sure the
    viewer has a current surface to send. ``alive`` says whether the viewer's session still
    stands; once it does not (signed out), the run ends with RUN_ERROR ``session_ended``."""
    emit = Emitter(journal, room.id, viewer, "feed", run_id=inp.run_id)
    presence: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    loop = asyncio.get_running_loop()
    wake = bus.subscribe(room.id)

    def put(ev: dict[str, Any]) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            presence.put_nowait(ev)
        wake.set()

    def on_presence(ev: dict[str, Any]) -> None:  # may run on any thread
        loop.call_soon_threadsafe(put, ev)

    bus.on_ephemeral(room.id, on_presence)
    try:
        yield emit(started(inp))
        if prepare is not None:
            prepare()
        snap, seq = room.snapshot_at()
        jseq = journal.store.journal_last_seq(room.id)
        yield emit(StateSnapshotEvent(snapshot=snap, metadata={"seq": seq}))
        latest: dict[str, dict[str, Any]] = {}  # the viewer's current surfaces, last one wins
        for entry in journal.entries(room.id):
            if entry["seq"] > jseq:
                break
            if entry["protocol"] == "A2UI" and entry.get("viewer") == viewer:
                latest[entry.get("refs", {}).get("surfaceId", "surface")] = entry
        for entry in latest.values():
            for ev in journal_events(entry, viewer):
                yield emit(ev)
        if on_open is not None:
            on_open()
        deadline = time.monotonic() + rotate_s
        last_out = time.monotonic()
        while True:
            wake.clear()  # before draining: a commit during the drain sets it again
            sent = False
            for row in room.store.log(room.id, after=seq):
                seq = row.seq
                for ev in row_events(row):
                    yield emit(ev)
                    sent = True
            for entry in journal.entries(room.id, after=jseq):
                jseq = entry["seq"]
                if entry["protocol"] == "AG-UI":
                    continue
                for ev in journal_events(entry, viewer):
                    yield emit(ev)
                    sent = True
            while not presence.empty():
                yield emit(CustomEvent(name="consilith.presence", value=presence.get_nowait()))
                sent = True
            if alive is not None and not alive():
                yield emit(run_error("this session has ended; sign in again", "session_ended"))
                return
            now = time.monotonic()
            if sent:
                last_out = now
            if now >= deadline:
                break
            if now - last_out >= keepalive_s:
                yield KEEPALIVE
                last_out = now
            if presence.empty():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=min(keepalive_s, 0.5))
        yield emit(
            RunFinishedEvent(
                thread_id=inp.thread_id, run_id=inp.run_id, outcome=RunFinishedCancelledOutcome()
            )
        )
    finally:
        bus.unsubscribe(room.id, wake)
        bus.off_ephemeral(room.id, on_presence)
