"""The Gemini key adapter: one ``generate_content`` call, text in and text out.

Read for this adapter, in the pinned ``google-genai==2.28.0``:

- ``google/genai/types.py``: ``FinishReason``, ``BlockedReason``,
  ``GenerateContentResponse`` (``candidates``, ``prompt_feedback``, ``usage_metadata``,
  ``model_version``, and the ``text`` property, which is ``Optional[str]``),
  ``GenerateContentResponseUsageMetadata``, ``HttpOptions`` (``timeout`` in milliseconds,
  ``retry_options``, ``httpx_async_client``) and ``HttpRetryOptions`` (``attempts``)
- ``google/genai/errors.py`` (``APIError`` has ``code``, ``status``, ``message``, ``details`` and
  ``response``; 4xx raise ``ClientError`` and 5xx ``ServerError``)
- ``google/genai/_api_client.py`` (``retry_args``: ``attempts`` of 0 or 1 means one attempt)

SDK retries: the SDK retries by default (5 attempts). The adapter sets ``attempts=1``, which this
SDK treats as no retry.

The text is joined from the text parts of the first candidate, leaving out thought parts. The
``text`` property is never turned into a string with ``str()``: it is ``None`` when a candidate
has no parts, as on a blocked reply, and ``str(None)`` would make the text "None".

Stop mapping (``Candidate.finish_reason`` to ``Completion.stop``):

=========================================================================  ==========
``STOP``                                                                   ``end``
``MAX_TOKENS``                                                             ``length``
``SAFETY``, ``BLOCKLIST``, ``PROHIBITED_CONTENT``, ``SPII``, ``RECITATION``,  ``filtered``
``IMAGE_SAFETY``, ``IMAGE_PROHIBITED_CONTENT``, ``IMAGE_RECITATION``
no candidate and ``prompt_feedback.block_reason`` set                      ``filtered``
no candidate and no block reason                                           ``empty``
any other reason (``OTHER``, ``LANGUAGE``, tool-call reasons, none)        ``end``
=========================================================================  ==========

An ``end`` stop with no text becomes ``empty``. Gemini has no refusal stop: a policy block is
``filtered``, never ``refusal``.

Usage: ``input_tokens`` is ``prompt_token_count``. ``output_tokens`` is
``candidates_token_count`` plus ``thoughts_token_count``, because thinking tokens are billed as
output and count against ``max_output_tokens``. ``model_used`` is ``model_version``.

Error mapping (``APIError.code`` and ``status``): 401 and 403, and a 400 whose status is
``INVALID_ARGUMENT`` with a message about the API key, are ``auth``; 429 is ``rate_limit`` with
``retry_after_s`` from the ``RetryInfo`` detail or the ``retry-after`` header, or ``quota`` when
the quota detail names a per-day limit or the message mentions billing; 500, 502 and 503 are
``overloaded``; 504 and a request timeout are ``timeout``; a connection
failure is ``network``; a 400 about the input token count is ``context_length``. Messages are
fixed text; the SDK message, which can echo the request, never crosses.

The client timeout sits under the request deadline. One HTTP connection pool is shared per event
loop; the SDK client that holds the key is built for one call and dropped when the call ends.
"""

from __future__ import annotations

from typing import Any

from ...platform.errors import ModelError, NotConfigured
from ..model_types import Completion, Request, StopReason, Usage
from . import register
from .base import _HTTPModel, make_error, shared_pool, text_of


def _pool() -> Any:
    """The shared HTTP connection pool for this event loop. It holds no key."""
    import httpx

    return shared_pool("gemini", httpx.AsyncClient)


def _make_client(key: str, timeout_s: float) -> Any:
    """A client for one call: no SDK retries, the shared pool, a timeout under the deadline."""
    try:
        from google import genai  # pyright: ignore[reportMissingImports, reportAttributeAccessIssue]
    except ImportError as exc:
        raise NotConfigured("the google-genai package is not installed") from exc
    return genai.Client(  # pyright: ignore[reportUnknownMemberType]
        api_key=key,
        http_options=genai.types.HttpOptions(  # pyright: ignore[reportUnknownMemberType]
            timeout=max(1, int(timeout_s * 1000)),
            retry_options=genai.types.HttpRetryOptions(attempts=1),  # pyright: ignore[reportUnknownMemberType]
            httpx_async_client=_pool(),
        ),
    )


_FILTERED = frozenset(
    {
        "SAFETY",
        "BLOCKLIST",
        "PROHIBITED_CONTENT",
        "SPII",
        "RECITATION",
        "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)


def _enum_name(value: Any) -> str:
    """``FinishReason.STOP`` and ``"STOP"`` both give ``"STOP"``."""
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return value if isinstance(value, str) else ""


def _text_of(candidate: Any) -> str:
    content = getattr(candidate, "content", None)
    texts: list[str] = []
    for part in getattr(content, "parts", None) or []:
        text = getattr(part, "text", None)
        if isinstance(text, str) and getattr(part, "thought", None) is not True:
            texts.append(text)
    return "".join(texts)


def _usage_of(resp: Any) -> Usage | None:
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return None
    i = getattr(u, "prompt_token_count", None)
    out = [
        n
        for n in (
            getattr(u, "candidates_token_count", None),
            getattr(u, "thoughts_token_count", None),
        )
        if isinstance(n, int)
    ]
    return Usage(
        input_tokens=i if isinstance(i, int) else None,
        output_tokens=sum(out) if out else None,
    )


def decode(resp: Any, fallback_model: str) -> Completion:
    """The ``Completion`` for one ``generate_content`` reply."""
    candidates = getattr(resp, "candidates", None) or []
    text = ""
    stop: StopReason
    if not candidates:
        blocked = _enum_name(getattr(getattr(resp, "prompt_feedback", None), "block_reason", None))
        stop = "filtered" if blocked and blocked != "BLOCKED_REASON_UNSPECIFIED" else "empty"
    else:
        first = candidates[0]
        text = _text_of(first)
        reason = _enum_name(getattr(first, "finish_reason", None))
        if reason == "MAX_TOKENS":
            stop = "length"
        elif reason in _FILTERED:
            stop = "filtered"
        else:
            stop = "end" if text.strip() else "empty"
    model = getattr(resp, "model_version", None)
    return Completion(
        text=text,
        stop=stop,
        usage=_usage_of(resp),
        model_used=model if isinstance(model, str) and model else fallback_model,
    )


def _retry_delay(exc: Any) -> float | None:
    """The wait the quota detail asks for (``RetryInfo.retryDelay``, like ``"7s"``)."""
    details = getattr(exc, "details", None)
    items = details.get("error", details).get("details", []) if isinstance(details, dict) else []
    for item in items if isinstance(items, list) else []:  # pyright: ignore[reportUnknownVariableType]
        delay = item.get("retryDelay") if isinstance(item, dict) else None  # pyright: ignore[reportUnknownMemberType]
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return max(0.0, float(delay[:-1]))
            except ValueError:
                pass
    return None


def _refine(exc: Exception) -> ModelError | None:
    """What only Gemini's failures say: an API-key 400, a per-day quota, the retry delay in the
    quota detail and a 400 about the input size. The SDK text is read for these tests and never
    kept. Everything else falls to the shared classifier."""
    code = getattr(exc, "code", None)
    status = code if isinstance(code, int) else 0
    low = text_of(getattr(exc, "message", "")).lower()
    details = str(getattr(exc, "details", "") or "").lower()
    if status == 400 and "api key" in low:
        return make_error("auth")
    if status == 429:
        if "perday" in details or "billing" in low:
            return make_error("quota")
        delay = _retry_delay(exc)
        if delay is not None:
            return make_error("rate_limit", retry_after_s=delay)
    if status == 400 and ("token count" in low or "too long" in low):
        return make_error("context_length")
    return None


def thinking_for(model: str, effort: str) -> dict[str, Any]:
    """Less thinking when the request asks for low effort: a thinking level on Gemini 3, a small
    thinking budget on 2.5; nothing otherwise."""
    if effort != "low":
        return {}
    if model.startswith("gemini-3"):
        return {"thinking_config": {"thinking_level": "low"}}
    if model.startswith("gemini-2.5"):
        return {"thinking_config": {"thinking_budget": 512}}
    return {}


@register("gemini")
class GeminiModel(_HTTPModel):
    name = "gemini"
    provider = "gemini"

    async def _attempt(self, req: Request) -> Completion:
        resp = await self.call_with_client(
            req,
            _make_client,
            lambda client: client.aio.models.generate_content(
                model=self.model,
                contents=req.input,
                config={
                    "system_instruction": req.instructions,
                    "max_output_tokens": req.max_output,
                    **thinking_for(self.model, req.effort),
                },
            ),
            _refine,
        )
        return decode(resp, self.model)
