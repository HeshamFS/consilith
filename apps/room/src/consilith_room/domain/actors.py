"""Who is acting. The gate decides from this, never from a prompt (FR-MP-7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ActorKind = Literal["person", "personal_agent", "room_agent", "backend"]
ROOM_AGENTS = ("conductor", "swarm", "solvers", "referee")


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    id: str
    owner: str | None = None  # the person a personal agent acts for
    initiator: str | None = None  # the person whose request started a delegation

    @staticmethod
    def person(pid: str) -> Actor:
        return Actor(kind="person", id=pid)

    @staticmethod
    def agent_of(pid: str) -> Actor:
        return Actor(kind="personal_agent", id=agent_id(pid), owner=pid, initiator=pid)

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
