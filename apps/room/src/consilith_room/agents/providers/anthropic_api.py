"""The Anthropic key adapter: one Messages call, text in and text out.

Read for this adapter, in the pinned ``anthropic==1.11.0``:

- ``anthropic/types/stop_reason.py`` and ``types/message.py`` (``stop_reason``, ``usage``,
  ``model``, ``content``)
- ``anthropic/_exceptions.py`` (the exception classes) and ``_client.py`` (``AsyncAnthropic``
  takes ``max_retries``, ``timeout`` and ``http_client``)

Stop mapping (``Message.stop_reason`` to ``Completion.stop``):

=============================================  =========================================
``end_turn``, ``stop_sequence``                ``end`` (``empty`` when no text came back)
``max_tokens``, ``model_context_window_exceeded``  ``length``
``refusal``                                    ``refusal``
``tool_use``, ``pause_turn``, ``None``         ``end`` (``empty`` when no text came back)
=============================================  =========================================

``filtered`` is never produced here: Anthropic reports a policy stop as ``refusal``. Tool calling
is not built in this release (JSON-action), so no tools are sent and ``tool_use`` is unexpected.

Usage: ``input_tokens`` counts the prompt tokens plus any cache read or cache write tokens, and
``output_tokens`` is the reported output count.

Error mapping (the exception class, then the status, to a ``ModelError``): 401 and 403 are
``auth``; 402 is ``quota``; 429 is ``rate_limit`` with ``retry_after_s`` from the ``retry-after``
header; 529, 503, 502 and 500 are ``overloaded``; 504 and a request timeout are
``timeout``; a dropped connection is ``network``; 413 and a 400 that says the prompt is too long
are ``context_length``. Messages are fixed text. The SDK message, which can echo request data,
never crosses.

SDK retries are off (``max_retries=0``): the adapter base owns the one retry policy. The client
timeout sits under the request deadline. One HTTP connection pool is shared per event loop; the
SDK client that holds the key is built for one call and dropped when the call ends.
"""

from __future__ import annotations

from typing import Any

from ...platform.errors import ModelError, NotConfigured
from ..model_types import Completion, Request, StopReason, Usage
from . import register
from .base import _HTTPModel, make_error, shared_pool, text_of


def _pool(sdk: Any) -> Any:
    """The shared HTTP connection pool for this event loop. It holds no key."""
    return shared_pool("anthropic", sdk.DefaultAsyncHttpxClient)


def _make_client(key: str, timeout_s: float) -> Any:
    """A client for one call: no SDK retries, the shared pool, a timeout under the deadline."""
    try:
        import anthropic  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise NotConfigured("the anthropic package is not installed") from exc
    return anthropic.AsyncAnthropic(  # pyright: ignore[reportUnknownMemberType]
        api_key=key, max_retries=0, timeout=timeout_s, http_client=_pool(anthropic)
    )


_STOPS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "tool_use": "end",
    "pause_turn": "end",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "refusal": "refusal",
}


def _text_of(msg: Any) -> str:
    # Only text blocks count; a thinking or tool block has no ``text`` string.
    texts: list[str] = []
    for block in getattr(msg, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "".join(texts)


def _usage_of(msg: Any) -> Usage | None:
    u = getattr(msg, "usage", None)
    if u is None:
        return None
    parts = [
        getattr(u, n, None)
        for n in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    ]
    seen = [p for p in parts if isinstance(p, int)]
    out = getattr(u, "output_tokens", None)
    return Usage(
        input_tokens=sum(seen) if seen else None,
        output_tokens=out if isinstance(out, int) else None,
    )


def decode(msg: Any, fallback_model: str) -> Completion:
    """The ``Completion`` for one Messages reply."""
    text = _text_of(msg)
    stop = _STOPS.get(str(getattr(msg, "stop_reason", None)), "end")
    if stop == "end" and not text.strip():
        stop = "empty"
    model = getattr(msg, "model", None)
    return Completion(
        text=text,
        stop=stop,
        usage=_usage_of(msg),
        model_used=model if isinstance(model, str) and model else fallback_model,
    )


def _refine(exc: Exception) -> ModelError | None:
    """What only this provider's failures say: a 400 about the prompt size (the SDK text is read
    for that one test and never kept)."""
    status = getattr(exc, "status_code", None)
    low = text_of(getattr(exc, "message", "")).lower()
    if status == 400 and ("too long" in low or "context window" in low):
        return make_error("context_length")
    return None


@register("anthropic")
class AnthropicModel(_HTTPModel):
    name = "anthropic"
    provider = "anthropic"

    async def _attempt(self, req: Request) -> Completion:
        msg = await self.call_with_client(
            req,
            _make_client,
            lambda client: client.messages.create(
                model=self.model,
                max_tokens=req.max_output,
                system=req.instructions,
                messages=[{"role": "user", "content": req.input}],
            ),
            _refine,
        )
        return decode(msg, self.model)
