"""The events one agent turn produces, and the runner that produces them.

``TurnRunner.run_turn`` yields ``TurnEvent`` values. The agent session maps them to AG-UI: text
becomes ``TEXT_MESSAGE_*``, tools become ``TOOL_CALL_*``, a surface becomes
``ACTIVITY_SNAPSHOT``, an approval request becomes the interrupt outcome, and a ``Notice`` is a
typed error with its code and fix action (not an opaque ``RUN_ERROR``).

Events are frozen and carry display facts only: they never carry a key, a prompt or another
person's text. They are in-process values, so numbers (counts, a fraction) are allowed here.

This module is the contract between the loop (which produces events) and the session and the
interface (which consume them). Fields can be added with defaults; none are removed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..domain.service import RoomService

# The names of the AG-UI custom events the session emits, one per event family.
EVENT_AGENT_MESSAGE = "consilith.agentmsg"
EVENT_NOTICE = "consilith.notice"

ToolStatus = Literal["ok", "error", "not_executed", "awaiting_approval", "cancelled"]
DoneStatus = Literal["answered", "awaiting_approval", "stopped", "failed"]


@dataclass(frozen=True)
class TextDelta:
    """A chunk of the agent's reply. Text is held per message, passed through ``{{result}}``
    substitution and the prose guard, then sent in chunks. A new ``message_id`` opens a new
    chat message; the previous one closes."""

    message_id: str
    text: str


@dataclass(frozen=True)
class ToolStart:
    """A tool card opens. ``call_id`` is the server's ``<step>.<index>``, not the model's.

    ``provider`` and ``model`` are the attribution shown in the card header: the interface only.
    """

    call_id: str
    tool: str  # the canonical ref
    label: str  # the human label for the card
    provider: str = ""
    model: str = ""


@dataclass(frozen=True)
class ToolArgs:
    """The card's arguments, redacted for display, each binding with its source and value."""

    call_id: str
    args: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class ToolEnd:
    """The call finished executing (the card's arguments and state are complete)."""

    call_id: str


@dataclass(frozen=True)
class ToolResult:
    """The outcome: a summary with ids, or a typed error and what to do about it."""

    call_id: str
    status: ToolStatus
    summary: str = ""  # server-built: ids and stamps, never model text
    result_ids: tuple[str, ...] = ()
    code: str | None = None  # a typed error's code
    fix_action: str | None = None


@dataclass(frozen=True)
class Surface:
    """A private surface for the person's chat: an A2UI message list."""

    surface_id: str
    messages: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ApprovalRequest:
    """A confirm card. ``action_id`` is the ``pending_actions`` id and the interrupt id.

    ``card`` is built by the server from the stored call: capability, mode, case and the lane
    inputs a run would read, never MCP arguments. An agent's own note, if any, sits under
    ``note`` and is shown as untrusted.
    """

    action_id: str
    kind: str  # a PendingActionKind value
    message: str
    card: dict[str, Any] = field(default_factory=dict[str, Any])
    note: str = ""
    expires: float = 0.0  # epoch seconds


@dataclass(frozen=True)
class Progress:
    """Something is happening. ``fraction`` is set only when the source reported one."""

    message: str
    call_id: str | None = None
    fraction: float | None = None


@dataclass(frozen=True)
class Notice:
    """A typed problem or limit, in plain words, with what the person can do about it."""

    code: str
    message: str
    fix_action: str | None = None


@dataclass(frozen=True)
class Done:
    """The turn is over. ``open_actions`` lists the approvals still waiting (confirm cards and
    proposals); the run then ends with the interrupt outcome."""

    message_id: str
    status: DoneStatus = "answered"
    open_actions: tuple[str, ...] = ()


TurnEvent = (
    TextDelta
    | ToolStart
    | ToolArgs
    | ToolEnd
    | ToolResult
    | Surface
    | ApprovalRequest
    | Progress
    | Notice
    | Done
)


@dataclass(frozen=True)
class TurnResume:
    """The person's answer to one open interrupt, as AG-UI resume entries carry it."""

    interrupt_id: str
    status: Literal["resolved", "cancelled"]
    payload: dict[str, Any] | None = None


class TurnRunner(Protocol):
    """Runs one turn and yields its events.

    ``msg_id`` is the client's message id, unique per person and room. ``resume`` answers open
    interrupts from an earlier turn. ``sid`` is the signed-in session that started the run, whose
    keys a model call may use.
    """

    def run_turn(
        self,
        room: RoomService,
        person: str,
        msg_id: str,
        text: str,
        *,
        resume: Sequence[TurnResume] | None = None,
        sid: str | None,
    ) -> AsyncIterator[TurnEvent]: ...
