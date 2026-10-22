"""Agent-to-agent messages. Agents never call each other: the conductor relays every message.

``PeerSend`` is the part a sending agent (a model) fills in. ``PeerEnvelope`` is what the
conductor builds around it and delivers to the recipient: every other field is set by the
conductor from trusted context and cannot be set by the sender. Numbers travel only as result
references, so ``PeerSend`` holds no numeric field.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import Contract

# The A2A data part that carries a PeerEnvelope.
PEER_MEDIA_TYPE = "application/vnd.consilith.peer+json"
# The A2A data part a recipient's peer turn answers with (the conductor posts it as the next
# message; the recipient's agent never posts by itself).
PEER_REPLY_MEDIA_TYPE = "application/vnd.consilith.peer-reply+json"
PEER_MAX_OPEN_THREADS = 8
PEER_MAX_TEXT = 1200
PEER_MAX_HOPS = 4
PEER_MAX_THREAD_MESSAGES = 12


class PeerKind(StrEnum):
    QUESTION = "question"
    ANSWER = "answer"
    FYI = "fyi"
    HANDOFF = "handoff"


class PeerRef(Contract):
    """Something a message points at, by id. A lane id is only meaningful inside its case."""

    kind: Literal["case", "lane", "result"]
    id: str = Field(min_length=1, max_length=200)
    case: str | None = Field(default=None, max_length=200)  # required for a lane, else absent

    @model_validator(mode="after")
    def lane_names_its_case(self) -> PeerRef:
        if self.kind == "lane" and not self.case:
            raise ValueError("a lane reference names its case")
        if self.kind != "lane" and self.case is not None:
            raise ValueError("only a lane reference names a case")
        return self


class PeerSend(Contract):
    """What the sending agent supplies: ``peer.send(to, kind, text, refs)`` minus the recipient.

    ``text`` is model prose and passes the prose guard (no numbers) before it is sent.
    """

    kind: PeerKind
    text: str = Field(max_length=PEER_MAX_TEXT)
    refs: list[PeerRef] = Field(default_factory=list[PeerRef])


class PeerParty(Contract):
    """An agent and the person it speaks for."""

    agent: str = Field(min_length=1)
    person: str = Field(min_length=1)


class PeerBudget(Contract):
    """What the thread and the recipient's owner can still absorb, set by the conductor.

    The limits are the platform's: 12 messages per thread, and the hourly peer turns per
    recipient owner (pooled) and per sender-to-recipient pair.
    """

    thread_messages_left: int = Field(ge=0)
    recipient_turns_left: int = Field(ge=0)
    pair_turns_left: int = Field(ge=0)


class PeerEnvelope(PeerSend):
    """A peer message as the recipient receives it. All fields beyond ``PeerSend`` are the
    conductor's and cannot be set by the sender.

    ``id`` is also the idempotency key: delivering the same id twice is one message. ``hop``
    counts agent-to-agent hops, the first message being hop 1, and a message past ``max_hops``
    is refused. ``trust`` is fixed: the text is untrusted data for the recipient, never an
    instruction.
    """

    id: str = Field(min_length=1)
    room: str = Field(min_length=1)
    thread: str = Field(min_length=1)
    in_reply_to: str | None = None
    from_: PeerParty = Field(alias="from")
    on_behalf_of: str = Field(min_length=1)  # the person the sending agent speaks for
    to: PeerParty
    hop: int = Field(ge=1)
    max_hops: int = Field(default=PEER_MAX_HOPS, ge=1)
    budget: PeerBudget
    trust: Literal["untrusted-peer"] = "untrusted-peer"

    @model_validator(mode="after")
    def hop_within_limit(self) -> PeerEnvelope:
        if self.hop > self.max_hops:
            raise ValueError("hop is past max_hops")
        return self


class PeerSendRequest(PeerSend):
    """What a sending agent hands the conductor: ``PeerSend`` plus the recipient. The thread,
    the hop, the reply link and the sender are never the sender's to set."""

    to: str = Field(min_length=1, max_length=200)  # a person id, or that person's agent id


class PeerPending(Contract):
    """A delivery the recipient's agent has not answered yet, for the interface's replying
    indicator. Ids only."""

    agent: str = Field(min_length=1)
    msg_id: str = Field(min_length=1)


class PeerNotice(Contract):
    """A calm, server-worded state of a thread: why nothing more happens now."""

    code: Literal["undeliverable", "capped", "paused", "declined", "closed", "refused"]
    text: str = Field(max_length=300)


class PeerThreadIndex(Contract):
    """The small record of one thread that the room document carries (at most 8 open threads).

    It holds ids, counters and states only, never a message body: bodies are fetched from the
    thread route by seated people. A change in ``count`` or ``last_ts`` tells the interface to
    fetch the new bodies.
    """

    id: str = Field(min_length=1)
    participants: list[str] = Field(min_length=2, max_length=2)  # the two people
    agents: list[str] = Field(min_length=2, max_length=2)  # their agents, in the same order
    opener: str = Field(min_length=1)  # the person whose agent sent the first message
    count: int = Field(ge=0)
    last_ts: float = Field(ge=0)
    status: Literal["open", "closed"] = "open"
    hop: int = Field(default=0, ge=0)  # the hop of the latest message
    max_hops: int = Field(default=PEER_MAX_HOPS, ge=1)
    max_messages: int = Field(default=PEER_MAX_THREAD_MESSAGES, ge=1)
    pending: PeerPending | None = None
    notice: PeerNotice | None = None
