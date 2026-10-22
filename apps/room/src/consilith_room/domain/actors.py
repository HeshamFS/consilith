"""Who is acting. The gate decides from this, never from a prompt (FR-MP-7)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

ActorKind = Literal["person", "personal_agent", "room_agent", "backend"]
ROOM_AGENTS = ("conductor", "swarm", "solvers", "referee")


@dataclass(frozen=True)
class Grant:
    """A single-use approval a person gave on a confirm card, carried by the actor that acts on it.

    It is minted only by the confirm-card path, never from model text. It is valid for one run
    of exactly the call it was approved for: ``digest`` must match a recomputation over the
    current case, mode, lane revisions and plugin pins, and ``expires_at`` (epoch seconds) must
    not have passed. A grant skips only the autonomy test; the drawer's tool allowlist and the
    compute budget still apply. ``authorize`` and ``open_run`` read it through
    ``RoomService.run_grant_valid``.
    """

    kind: str  # what was approved; "run" is the only kind in use
    digest: str  # the card's digest (consilith_contracts.pending_action_digest)
    approved_by: str  # the person who approved
    # (lane path, revision digest) pairs, sorted. A tuple so that the actor stays hashable.
    revisions: tuple[tuple[str, str], ...] = ()
    expires_at: float = 0.0
    case: str = ""  # the capability the card approved
    mode: str = ""  # and its mode

    @staticmethod
    def of(
        kind: str,
        digest: str,
        approved_by: str,
        revisions: Mapping[str, str],
        expires_at: float,
        case: str = "",
        mode: str = "",
    ) -> Grant:
        return Grant(
            kind, digest, approved_by, tuple(sorted(revisions.items())), expires_at, case, mode
        )

    def revisions_map(self) -> dict[str, str]:
        return dict(self.revisions)

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str
    owner: str | None = None  # the person a personal agent acts for
    initiator: str | None = None  # the person whose request started a delegation
    grant: Grant | None = None  # a confirmed approval for one action; None unless a card gave it

    @staticmethod
    def person(pid: str) -> Actor:
        return Actor(kind="person", id=pid)

    @staticmethod
    def agent_of(pid: str) -> Actor:
        return Actor(kind="personal_agent", id=agent_id(pid), owner=pid, initiator=pid)

    def with_grant(self, grant: Grant | None) -> Actor:
        """This actor carrying a card's approval (or none)."""
        return replace(self, grant=grant)

    @staticmethod
    def room_agent(name: str, initiator: str | None = None) -> Actor:
        if name not in ROOM_AGENTS:
            raise ValueError(f"unknown room agent {name!r}")
        return Actor(kind="room_agent", id=name, initiator=initiator)

    @staticmethod
    def backend() -> Actor:
        return Actor(kind="backend", id="backend")

    @property
    def person_id(self) -> str | None:
        """The person this actor speaks for, if any."""
        if self.kind == "person":
            return self.id
        if self.kind == "personal_agent":
            return self.owner
        return self.initiator


def agent_id(pid: str) -> str:
    return f"{pid}.agent"
