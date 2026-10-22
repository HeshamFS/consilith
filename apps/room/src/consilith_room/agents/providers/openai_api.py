"""The OpenAI Responses API key adapter: one ``responses.create`` call, text in and text out.

Read for this adapter, in the pinned ``openai==3.26.0``:

- ``openai/types/responses/response.py`` (``status``, ``incomplete_details``, ``usage``,
  ``model``, ``error``, ``output_text``), ``response_status.py``, ``response_error.py`` and
  ``response_output_refusal.py``
- ``openai/resources/responses/responses.py`` (``create`` accepts ``store``,
  ``max_output_tokens``, ``instructions`` and ``input``)
- ``openai/_exceptions.py`` (the exception classes) and ``_client.py`` (``AsyncOpenAI`` takes
  ``max_retries``, ``timeout`` and ``http_client``)

Request storage: ``create(store=...)`` is accepted by this SDK and defaults to true (responses
are kept for at least 30 days). The adapter sends ``store=False`` on every call.

The Responses API needs ``max_output_tokens`` of at least 16, so a smaller request value is
raised to 16. Reasoning tokens count against the cap; the model table keeps headroom for them.

Stop mapping (``Response.status`` and ``incomplete_details.reason`` to ``Completion.stop``):

====================================================  ===================================
``completed``, ``None`` with text                     ``end``
``completed`` and no text                             ``empty``
``completed`` with a refusal part and no text         ``refusal``
``incomplete`` + ``max_output_tokens``, ``max_messages``  ``length``
``incomplete`` + ``content_filter``, ``steered``      ``filtered``
``incomplete`` + any other reason                     ``length``
``cancelled``, ``queued``, ``in_progress`` and no text  ``empty``
``failed``                                            raises (see below)
====================================================  ===================================

A ``failed`` response carries ``error.code``: ``rate_limit_exceeded`` is ``rate_limit``,
``server_error`` is ``overloaded``, the policy codes (``bio_policy``,
``misalignment_policy_violation``, ``image_content_policy_violation``) are ``refused``, and any
other code is a generic ``ModelError``.

Usage: ``input_tokens`` and ``output_tokens`` as reported (output includes reasoning tokens).
``output_text`` is used for the text; it joins only ``output_text`` parts and returns an empty
string when there are none, so no ``str()`` of an SDK value is made.

Error mapping (exception class, then status): 401 and 403 are ``auth``; 429 is ``rate_limit``
with ``retry_after_s`` from the ``retry-after`` header, or ``quota`` when the error code is
``insufficient_quota``; 500, 502, 503 and 529 are ``overloaded``; 504 and a request
timeout are ``timeout``; a dropped connection is ``network``; 413 and a 400 with the code
``context_length_exceeded`` are ``context_length``. Messages are fixed text; the SDK message never
crosses.

SDK retries are off (``max_retries=0``). The client timeout sits under the request deadline. One
HTTP connection pool is shared per event loop; the SDK client that holds the key is built for one
call and dropped when the call ends.
"""

from __future__ import annotations

from typing import Any

from ...platform.errors import ModelError, NotConfigured
from ..model_types import Completion, Request, StopReason, Usage
from . import register
from .base import _HTTPModel, make_error, shared_pool

_MIN_OUTPUT = 16


def _pool(sdk: Any) -> Any:
    """The shared HTTP connection pool for this event loop. It holds no key."""
    return shared_pool("openai", sdk.DefaultAsyncHttpxClient)


def _make_client(key: str, timeout_s: float) -> Any:
    """A client for one call: no SDK retries, the shared pool, a timeout under the deadline."""
    try:
        import openai  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise NotConfigured("the openai package is not installed") from exc
    return openai.AsyncOpenAI(  # pyright: ignore[reportUnknownMemberType]
        api_key=key, max_retries=0, timeout=timeout_s, http_client=_pool(openai)
    )


_INCOMPLETE: dict[str, StopReason] = {
    "max_output_tokens": "length",
    "max_messages": "length",
    "content_filter": "filtered",
    "steered": "filtered",
}
_POLICY_CODES = frozenset(
    {"bio_policy", "misalignment_policy_violation", "image_content_policy_violation"}
)


def _has_refusal(resp: Any) -> bool:
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                return True
    return False


def _usage_of(resp: Any) -> Usage | None:
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    i, o = getattr(u, "input_tokens", None), getattr(u, "output_tokens", None)
    return Usage(
        input_tokens=i if isinstance(i, int) else None,
        output_tokens=o if isinstance(o, int) else None,
    )


def decode(resp: Any, fallback_model: str) -> Completion:
    """The ``Completion`` for one Responses reply. A ``failed`` response raises."""
    status = getattr(resp, "status", None)
    if status == "failed":
        raise _failed(getattr(getattr(resp, "error", None), "code", None))
    text_raw = getattr(resp, "output_text", None)
    text = text_raw if isinstance(text_raw, str) else ""
    stop: StopReason
    if status == "incomplete":
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        stop = _INCOMPLETE.get(str(reason), "length")
    elif not text.strip():
        stop = "refusal" if _has_refusal(resp) else "empty"
    else:
        stop = "end"
    model = getattr(resp, "model", None)
    return Completion(
        text=text,
        stop=stop,
        usage=_usage_of(resp),
        model_used=model if isinstance(model, str) and model else fallback_model,
    )


def _failed(code: Any) -> ModelError:
    if code == "rate_limit_exceeded":
        return make_error("rate_limit")
    if code == "server_error":
        return make_error("overloaded")
    if code in _POLICY_CODES:
        return make_error("refused")
    return make_error("model_error")


def reasoning_for(model: str, effort: str) -> dict[str, Any]:
    """The reasoning effort for models that take one (gpt-5 and later, o-series); nothing for the
    rest, which would reject the field."""
    if effort == "provider_default":
        return {}
    if not (model.startswith(("gpt-5", "gpt-6", "gpt-7")) or model[:2] in {"o1", "o3", "o4"}):
        return {}
    return {"reasoning": {"effort": effort}}


@register("openai")
class OpenAIModel(_HTTPModel):
    name = "openai"
    provider = "openai"

    async def _attempt(self, req: Request) -> Completion:
        resp = await self.call_with_client(
            req,
            _make_client,
            lambda client: client.responses.create(
                model=self.model,
                instructions=req.instructions,
                input=req.input,
                max_output_tokens=max(req.max_output, _MIN_OUTPUT),
                store=False,
                **reasoning_for(self.model, req.effort),
            ),
        )
        return decode(resp, self.model)
