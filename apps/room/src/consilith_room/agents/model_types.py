"""The model port, version 2: one request shape and one completion shape for every provider.

A fixed ``Request`` produces the same rendered input for all providers, and each adapter turns
its provider's reply into a ``Completion`` or raises a ``ModelError`` (platform/errors.py). These
are internal types, not wire contracts: nothing here crosses a process boundary, and no model
fills them, so numbers (token counts, a deadline) are allowed.

The provider and the model actually used are recorded in ``Completion.model_used`` and shown in
the interface. They never go into a prompt.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

M = TypeVar("M", bound=BaseModel)

# Why a reply ended. ``refusal`` and ``filtered`` become a fixed notice and never chat text. A
# ``length`` stop discards any calls in that reply. ``empty`` means no text came back.
StopReason = Literal["end", "length", "refusal", "filtered", "empty"]
Effort = Literal["provider_default", "low", "medium", "high"]


class Usage(BaseModel):
    """Tokens, only where the provider reports them (ChatGPT reports none)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class Completion(BaseModel):
    """What one model call returned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    stop: StopReason
    usage: Usage | None = None
    model_used: str  # the model the provider says it ran


class Request(BaseModel):
    """One model call. ``instructions`` holds the rules only. ``input`` is a single user message
    that holds the transcript, the briefing, the tool block and the person's message, built by
    one prompt builder for every provider. No sampling fields are sent to any provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: str
    input: str
    max_output: int = Field(gt=0)  # clamped by the model table
    effort: Effort = "provider_default"
    deadline_s: float = Field(gt=0)
    principal: str  # the person id the call is made for and metered against
    trace_id: str


@runtime_checkable
class ModelPortV2(Protocol):
    """What an adapter provides. ``reply`` and ``structured`` are shims over ``complete``:
    ``structured`` is prompted JSON plus validation plus repair, the same for every provider."""

    provider: str
    model: str

    async def complete(self, req: Request) -> Completion: ...

    async def reply(self, system: str, text: str) -> str: ...

    async def structured(self, system: str, text: str, schema: type[M]) -> M: ...
