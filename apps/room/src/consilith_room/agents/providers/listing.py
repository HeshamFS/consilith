"""Listing the models a key (or the ChatGPT sign-in) can use.

One sort for all four providers (newest first where the provider gives a date, otherwise by id,
descending) and one error mapping. The ChatGPT branch keeps the helper's own safe messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ...platform.errors import ModelError, NotConfigured
from .base import classify_exception, error_message, make_error

# Chat models only: OpenAI lists embeddings, audio, image and moderation models too.
_OPENAI_CHAT = re.compile(r"^(gpt-|o\d|chatgpt-)")
_OPENAI_NOT_CHAT = re.compile(
    r"(audio|realtime|transcribe|tts|image|embedding|search|moderation|dall-e|whisper|instruct)"
)


@dataclass(frozen=True)
class _Row:
    id: str
    label: str
    created: float  # 0.0 when the provider gives no date


def _stamp(value: object) -> float:
    """A provider date as seconds: a datetime, a number, or an ISO string. 0.0 when unknown."""
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _ordered(rows: list[_Row]) -> list[dict[str, str]]:
    """The one sort: newest first, then by id descending (also the order when no dates)."""
    rows.sort(key=lambda r: (r.created, r.id), reverse=True)
    return [{"id": r.id, "label": r.label} for r in rows]


_LABELS = {"anthropic": "Anthropic", "openai": "OpenAI", "gemini": "Google Gemini"}


def _refused(provider: str, exc: Exception) -> ModelError:
    """The one error mapping, in plain words for the person. The SDKs put request details in
    their messages, so none of it goes out, and the key never does. A refusal the table does not
    name reads as the key; a busy, slow or unreachable provider keeps the table's own words."""
    mapped = classify_exception(exc)
    if mapped.code != "model_error" and mapped.code != "auth":
        return mapped
    label = _LABELS.get(provider, provider)
    fixed = make_error(
        "auth",
        message=(
            f"{label} did not accept this key. Check that you copied all of it, or make a new one."
        ),
        retry_after_s=mapped.retry_after_s,
    )
    fixed.fix_action = "reenter_key"
    return fixed


async def list_models(provider: str, key: str, person: str | None = None) -> list[dict[str, str]]:
    """The models this key can use, newest first, as ``[{id, label}]``. The provider is asked
    directly, so new models appear without a release of ours. The key is never in an error.
    ``person`` is accepted for the per-account listing that comes later; it is unused now."""
    if provider == "siwc":
        # The helper's safe messages pass through unchanged.
        from .. import models as _models

        siwc = _models.SIWC
        if siwc is None:
            raise NotConfigured("Sign in with ChatGPT is not set up on this server")
        listed = await siwc.call("GET", "/models")
        return _ordered(
            [
                _Row(str(m["id"]), str(m.get("label") or m["id"]), 0.0)
                for m in listed.get("models", [])
            ]
        )
    try:
        if provider == "anthropic":
            import anthropic

            client: Any = anthropic.AsyncAnthropic(api_key=key)
            return _ordered(
                [
                    _Row(
                        m.id,
                        getattr(m, "display_name", None) or m.id,
                        _stamp(getattr(m, "created_at", None)),
                    )
                    async for m in client.models.list(limit=100)
                ]
            )
        if provider == "openai":
            import openai

            oclient: Any = openai.AsyncOpenAI(api_key=key)
            return _ordered(
                [
                    _Row(m.id, m.id, _stamp(getattr(m, "created", None)))
                    async for m in oclient.models.list()
                    if _OPENAI_CHAT.match(m.id) and not _OPENAI_NOT_CHAT.search(m.id)
                ]
            )
        if provider == "gemini":
            from google import genai  # pyright: ignore[reportMissingImports, reportAttributeAccessIssue]

            gclient: Any = genai.Client(api_key=key)
            rows: list[_Row] = []
            async for m in await gclient.aio.models.list():
                actions = getattr(m, "supported_actions", None) or []
                name = getattr(m, "name", "")
                name = name if isinstance(name, str) else ""
                if "generateContent" not in actions or not name:
                    continue
                mid = name.removeprefix("models/")
                label = getattr(m, "display_name", None)
                rows.append(_Row(mid, label if isinstance(label, str) and label else mid, 0.0))
            return _ordered(rows)
    except Exception as exc:
        raise _refused(provider, exc) from None
    raise NotConfigured(
        f"{provider} has no model list", user_message=error_message("not_configured")
    )
