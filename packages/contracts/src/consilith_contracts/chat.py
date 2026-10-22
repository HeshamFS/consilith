"""The durable chat history: one row per turn, per person, per room (``chat_turns``).

The history lives on the server and is keyed by the authenticated person only. A retry of the
same client message id never runs the agent twice: the status machine below decides whether to
replay the stored reply, say that the turn is in progress, or resume it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from .base import Contract


class TurnStatus(StrEnum):
    """``admitted -> answering -> answered | failed | superseded``."""

    ADMITTED = "admitted"
    ANSWERING = "answering"
    ANSWERED = "answered"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class TurnRole(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    PEER = "peer"
    SYSTEM = "system"


class ModelCallMeta(Contract):
    """What ``ChatTurn.meta`` records about one model call. No secrets, no prompt text.

    This is the only place the provider and the model actually used are stored. They are shown
    in the interface and never put in a prompt, a peer envelope or an agent card.
    """

    provider: str
    model_used: str
    transport: str  # for example ``json-action``
    exposure_digest: str  # hash of the rendered tool block
    stop: str  # the stop reason of the completion


class ChatTurn(Contract):
    """One row of ``chat_turns``, unique on ``(room, person, msg_id)``."""

    room: str = Field(min_length=1)
    person: str = Field(min_length=1)
    agent: str = Field(min_length=1)  # the person's agent id
    seq: int = Field(default=0, ge=0)  # per (room, person); assigned by the store on insert
    ts: float = Field(default=0, ge=0)  # epoch seconds; assigned by the store on insert
    role: TurnRole
    run_id: str | None = None
    msg_id: str = Field(min_length=1)  # the client's message id
    status: TurnStatus = TurnStatus.ADMITTED
    # Whether the turn has read class-B text. It persists, so a confirm-card resume keeps it.
    taint: bool = False
    text: str = ""  # passed through ``journal.redact`` before it is stored
    # Model calls, tool entries (digests only), the actions digest. Keys are the writer's.
    meta: dict[str, Any] = Field(default_factory=dict[str, Any])
