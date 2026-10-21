"""Request bodies. Strict: unknown fields are refused."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EmailIn(Body):
    email: str = Field(max_length=320)


class TokenIn(Body):
    token: str = Field(min_length=1, max_length=512)


class TicketIn(Body):
    ticket: str = Field(min_length=1, max_length=512)


class ProfileIn(Body):
    name: str = Field(min_length=1, max_length=60)
    color: Literal["blue", "magenta", "teal", "violet", "amber", "olive"]
    discipline: str = Field(default="", max_length=100)


class KeyIn(Body):
    """A model key. ``hide_input_in_errors`` and SecretStr keep it out of validation errors."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    provider: Literal["anthropic", "openai", "gemini"]
    key: SecretStr = Field(min_length=8, max_length=512)


class AgentChoiceIn(Body):
    kind: Literal["consilith", "brought"] = "consilith"
    card_url: str | None = Field(default=None, alias="cardUrl", max_length=2048)


class NewRoomIn(Body):
    id: str
    title: str = Field(min_length=1, max_length=120)
    studio: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    invite: list[str] = Field(default_factory=list[str], max_length=10)
    # Working alone: the creator also takes the other role's lanes until a colleague joins.
    alone: bool = False


class NewInviteIn(Body):
    email: str | None = Field(default=None, max_length=320)


class AcceptIn(Body):
    role: str = Field(min_length=1, max_length=100)
    agent: AgentChoiceIn = Field(default_factory=AgentChoiceIn)
