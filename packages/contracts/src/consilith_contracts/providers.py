"""The provider registry: the one list of model providers every layer reads.

Provider names used to be hard-coded in five server places and three web copies. They are now
data: a ``ProviderDescriptor`` per provider, and ``str`` fields that are checked against this
registry. Adding a provider means adding one entry here (plus its adapter), not editing the
drawer, the preferences, the key route and the connect panel one by one.

Stored values stay valid: every name that ever existed (``scripted``, ``anthropic``, ``openai``,
``gemini``, ``siwc``) is in the registry. An unknown name is refused with a message that lists
the known ones, and never echoes more than a short prefix of what was sent.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from .base import Contract

# How a person connects the provider:
#   key      an API key the person types; it lives in server memory for the session
#   chatgpt  the person's own ChatGPT plan, through the sign-in sidecar
#   brought  an agent the person brings (A2A); no registry entry uses it in this release
#   none     nothing to connect (the scripted model)
ProviderAuth = Literal["key", "chatgpt", "brought", "none"]


class UnknownProviderError(ValueError):
    """``get_provider`` was asked for a name that is not in the registry."""


class ProviderDescriptor(Contract):
    """One model provider, as the connect panel, the drawer and the validators see it."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    label: str = Field(min_length=1, max_length=60)
    auth: ProviderAuth
    # Every real provider needs an explicit model. Only ``scripted`` does not.
    needs_model: bool
    # A picker suggestion, never a silent fallback. Only providers with a model may have one.
    default_model: str | None = Field(default=None, min_length=1, max_length=100)
    # One plain line on what leaves the host when this provider answers.
    data_terms: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def check_fields(self) -> ProviderDescriptor:
        if self.default_model is not None and not self.needs_model:
            raise ValueError("a provider that needs no model cannot suggest one")
        if "\n" in self.data_terms or "\r" in self.data_terms:
            raise ValueError("data_terms is one line")
        return self


PROVIDERS: Mapping[str, ProviderDescriptor] = MappingProxyType(
    {
        p.id: p
        for p in (
            ProviderDescriptor(
                id="scripted",
                label="No model",
                auth="none",
                needs_model=False,
                data_terms="Nothing leaves this host: replies come from built-in rules.",
            ),
            ProviderDescriptor(
                id="anthropic",
                label="Anthropic",
                auth="key",
                needs_model=True,
                default_model="claude-sonnet-5-5",
                data_terms=(
                    "Your messages and room context are sent to Anthropic with your API key."
                ),
            ),
            ProviderDescriptor(
                id="openai",
                label="OpenAI",
                auth="key",
                needs_model=True,
                default_model="gpt-6.1-sol",
                data_terms="Your messages and room context are sent to OpenAI with your API key.",
            ),
            ProviderDescriptor(
                id="gemini",
                label="Google Gemini",
                auth="key",
                needs_model=True,
                default_model="gemini-3.8-flash",
                data_terms="Your messages and room context are sent to Google with your API key.",
            ),
            ProviderDescriptor(
                id="siwc",
                label="Your ChatGPT plan",
                auth="chatgpt",
                needs_model=True,
                data_terms=(
                    "Your messages and room context are sent to OpenAI through your own "
                    "ChatGPT plan."
                ),
            ),
        )
    }
)


def provider_ids() -> tuple[str, ...]:
    """Every registered provider id, in registry order."""
    return tuple(PROVIDERS)


def keyed_provider_ids() -> tuple[str, ...]:
    """The providers a person connects with an API key, in registry order."""
    return tuple(p.id for p in PROVIDERS.values() if p.auth == "key")


def get_provider(provider_id: str) -> ProviderDescriptor:
    """The descriptor for ``provider_id``; ``UnknownProviderError`` (a ``ValueError``) if none."""
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise UnknownProviderError(_unknown(provider_id)) from None


def is_provider(provider_id: str) -> bool:
    return provider_id in PROVIDERS


def _shown(value: str) -> str:
    # A provider field never carries a secret, but a misplaced key must not be echoed back.
    return repr(value if len(value) <= 24 else value[:24] + "...")


def _unknown(value: str) -> str:
    return f"unknown model provider {_shown(value)}; use one of: {', '.join(PROVIDERS)}"


def _check_provider(value: str) -> str:
    if value not in PROVIDERS:
        raise ValueError(_unknown(value))
    return value


def _check_keyed_provider(value: str) -> str:
    descriptor = PROVIDERS.get(value)
    if descriptor is None:
        raise ValueError(_unknown(value))
    if descriptor.auth != "key":
        keyed = ", ".join(keyed_provider_ids())
        raise ValueError(f"{value!r} does not take an API key; keyed providers: {keyed}")
    return value


# A ``str`` that must name a registered provider. Use it for stored and wire fields.
ProviderId = Annotated[str, AfterValidator(_check_provider)]
# A ``str`` that must name a registered provider whose auth is ``key``.
KeyedProviderId = Annotated[str, AfterValidator(_check_keyed_provider)]
