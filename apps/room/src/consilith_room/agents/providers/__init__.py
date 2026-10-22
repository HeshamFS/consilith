"""The provider adapters, one module each, found through a registry.

An adapter registers itself with ``@register("<id>")``; ``adapter_for`` looks it up. Importing
this package imports every adapter module, so the registry is always complete.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T", bound=type)

ADAPTERS: dict[str, type[Any]] = {}


def register(provider_id: str) -> Callable[[T], T]:
    def deco(cls: T) -> T:
        ADAPTERS[provider_id] = cls
        return cls

    return deco


def adapter_for(provider_id: str) -> type[Any]:
    """The adapter class for a provider id. An unknown id raises ``KeyError``."""
    return ADAPTERS[provider_id]


from . import anthropic_api, gemini_api, openai_api, scripted, siwc  # noqa: E402,F401
