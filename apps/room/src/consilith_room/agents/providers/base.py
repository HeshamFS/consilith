"""The model port, version 2, and everything the provider adapters share.

This module is the stable API of the model layer. The adapters (``anthropic_api``, ``openai_api``,
``gemini_api``, ``siwc``, ``scripted``) build on it, and ``agents/models.py`` re-exports its names.

What an adapter author needs
----------------------------
Subclass ``_HTTPModel`` and implement one method::

    async def _attempt(self, req: Request) -> Completion

``_attempt`` makes exactly one provider request and returns a ``Completion``. It does not retry,
time out, or interpret stops: ``complete()`` (here) wraps it in the shared policy.

* Key: ``self.current_key()`` returns the key for this call and raises ``NotConfigured`` when it
  is gone (sign-out, key removal). Build the SDK client inside ``_attempt``, with SDK retries off
  (``max_retries=0``), and let it go out of scope when the call ends. Never store a client that
  holds a key.
* Errors: wrap SDK calls with ``except Exception as exc: raise map_sdk_error(exc, self.provider)
  from None``. It reads only the exception type, a status code and a ``retry-after`` header, and
  returns a ``ModelError`` with a fixed message. Never put an SDK message, a key or a prompt in
  an error.
* Text: ``text_of(value)`` returns the value only when it already is a ``str`` and ``""`` otherwise.
  Never call ``str()`` on an SDK value.
* Stops: return ``stop="refusal"`` or ``"filtered"`` when the provider blocked the reply,
  ``"length"`` for an output-limit stop, ``"empty"`` when nothing came back, ``"end"`` otherwise.
* ``model_used`` is the model the provider says it ran, or ``self.model`` when it does not say.

The shared policy (``complete_with_policy``, section 3 of the build spec)
-------------------------------------------------------------------------
* Every attempt runs under ``asyncio.timeout(min(ATTEMPT_CEILING_S, time left))``.
* A ``network``, ``overloaded`` or ``rate_limit`` error whose ``retry_after_s`` is 10 or less (or
  unknown) gets exactly one retry, after waiting that long. ``auth``, ``quota`` and the rest never
  retry. Every attempt calls the meter's hook, so retries and repairs count as model calls.
* A refusal or filter raises ``RefusedError``. An empty reply gets one retry, then
  ``EmptyReplyError``. A ``length`` stop is returned as is: ``structured()`` and the JSON-action
  loop raise ``TruncatedError`` for it, and plain ``reply()`` text is cut to the visible-text cap.

The shims (``PortShims``): ``reply()`` and ``structured()`` over ``complete()``. ``structured()``
runs ``structured_with_retry``: prompted JSON, ``raw_decode`` from the first ``{``, pydantic
validation, up to two repairs that carry the error back.

Compatibility: an adapter written for the old port that overrides ``reply(system, text)`` still
works. ``_attempt`` then calls that ``reply`` once, under the same policy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Protocol, TypeVar

from consilith_contracts.agent_tools import assert_structured_schema
from pydantic import BaseModel, ValidationError

from ...platform.errors import (
    BadOutputError,
    EmptyReplyError,
    ModelError,
    NotConfigured,
    TruncatedError,
    error_for_code,
)
from ..model_types import Completion, ModelPortV2, Request

__all__ = [
    "ATTEMPT_CEILING_S",
    "ATTEMPT_HOOK",
    "CURRENT_PRINCIPAL",
    "DEFAULT_DEADLINE_S",
    "RETRY_MAX_WAIT_S",
    "STRUCTURED_OUTPUT_TOKENS",
    "VISIBLE_TEXT_CAP",
    "Completion",
    "M",
    "ModelPort",
    "ModelPortV2",
    "PortShims",
    "Request",
    "_HTTPModel",
    "assert_no_numbers",
    "cap_visible_text",
    "check_stop",
    "classify_exception",
    "client_timeout",
    "complete_with_policy",
    "error_message",
    "first_json_object",
    "log_model_error",
    "make_error",
    "map_sdk_error",
    "run_with_policy",
    "shared_pool",
    "structured_with_retry",
    "text_of",
]

M = TypeVar("M", bound=BaseModel)
_OUTPUT_BUDGET: ContextVar[int] = ContextVar("model_output_budget", default=800)

log = logging.getLogger("consilith.models")

# --- the shared policy numbers --------------------------------------------------------------

ATTEMPT_CEILING_S = 170.0  # one attempt never runs longer than this
DEFAULT_DEADLINE_S = 190.0  # what the shims ask for; the bridge call is bounded at 180 s
RETRY_MAX_WAIT_S = 10.0  # a provider that asks for a longer wait is not retried
VISIBLE_TEXT_CAP = 6000  # characters of visible text, enforced host-side for all four providers
STRUCTURED_OUTPUT_TOKENS = 8192  # the output budget of a structured() call

# The person a call is made for, for the shims. The resolver's bound port sets it per call, so a
# bare adapter used from a test falls back to "".
CURRENT_PRINCIPAL: ContextVar[str] = ContextVar("model_principal", default="")
# Called once per provider attempt, before it starts. The meter installs it to count attempts and
# to refuse an attempt when the hourly cap is used up. It may raise a ``ModelError``.
ATTEMPT_HOOK: ContextVar[Callable[[], None] | None] = ContextVar("model_attempt_hook", default=None)

# --- one message table ----------------------------------------------------------------------

# code -> (words the person reads, the action the interface can offer). Chat, the composer and
# dispatch all read this table, so one failure reads the same everywhere.
_MESSAGES: dict[str, tuple[str, str | None]] = {
    "auth": ("The provider did not accept your credential. Connect it again.", "reenter_key"),
    "rate_limit": ("The provider is rate limiting your requests. Try again shortly.", "retry"),
    "quota": ("Your plan or key has no allowance left for this model.", "check_plan"),
    "overloaded": ("The provider is busy. Try again in a moment.", "retry"),
    "network": ("The provider could not be reached. Try again.", "retry"),
    "timeout": ("The model took too long to answer. Try again.", "retry"),
    "context_length": ("The conversation is too long for this model.", "choose_model"),
    "bad_output": ("The model did not answer in a usable form. Try again.", "retry"),
    "refused": ("The model declined to answer this request.", None),
    "truncated": ("The model's answer was cut off. Try again.", "retry"),
    "empty": ("The model returned no answer. Try again.", "retry"),
    "cancelled": ("The request was cancelled.", None),
    "not_configured": (
        "No model is connected for your agent. Connect one in Your agent.",
        "connect",
    ),
    "model_error": ("The model call failed. Try again.", "retry"),
}


def error_message(code: str) -> str:
    """The fixed words for an error code."""
    return _MESSAGES.get(code, _MESSAGES["model_error"])[0]


def make_error(
    code: str,
    *,
    retry_after_s: float | None = None,
    message: str | None = None,
    fix_action: str | None = None,
) -> ModelError:
    """The ``ModelError`` for a code, with the fixed message and fix action of the table."""
    words, action = _MESSAGES.get(code, _MESSAGES["model_error"])
    text = message or words
    chosen = fix_action if fix_action is not None else action
    if code in _MESSAGES and code != "model_error":
        return error_for_code(code, text, fix_action=chosen, retry_after_s=retry_after_s)
    return ModelError(text, fix_action=chosen, retry_after_s=retry_after_s)


def _status_of(exc: BaseException) -> int | None:
    for name in ("status_code", "status", "http_status", "code"):
        value = getattr(exc, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def log_model_error(exc: BaseException, *, provider: str = "") -> None:
    """Log the exception type and the status code only. Never the message: SDK messages can echo
    request details, and a key must never reach a log line."""
    status = _status_of(exc)
    log.warning(
        "model call failed: provider=%s type=%s status=%s",
        provider or "?",
        type(exc).__name__,
        status if status is not None else "-",
    )


# --- classifying SDK failures ---------------------------------------------------------------


def _retry_after_of(exc: BaseException) -> float | None:
    response: Any = getattr(exc, "response", None)
    headers: Any = getattr(response, "headers", None)
    raw: Any = None
    if headers is not None:
        try:
            raw = headers.get("retry-after")
        except Exception:
            raw = None
    if raw is None:
        raw = getattr(exc, "retry_after", None)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(0.0, float(raw))
    if isinstance(raw, str):
        try:
            return max(0.0, float(raw))
        except ValueError:
            return None
    return None


def _marker_of(exc: BaseException) -> str:
    """Short machine words the SDK attaches to a failure (a code or type name), lower case. Never
    the human message."""
    parts: list[str] = []
    for name in ("code", "type", "error_code"):
        value = getattr(exc, name, None)
        if isinstance(value, str):
            parts.append(value)
    body: Any = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner: Any = body.get("error", body)  # pyright: ignore[reportUnknownMemberType]
        if isinstance(inner, dict):
            for name in ("code", "type", "status"):
                value = inner.get(name)  # pyright: ignore[reportUnknownMemberType]
                if isinstance(value, str):
                    parts.append(value)
    return " ".join(parts).lower()


def classify_exception(exc: BaseException) -> ModelError:
    """A ``ModelError`` for any provider or transport failure. Only the type name, a status code,
    a few machine markers and a ``retry-after`` header are read; the message is never used."""
    if isinstance(exc, ModelError):
        return exc
    status = _status_of(exc)
    marker = _marker_of(exc)
    kind = type(exc).__name__.lower()
    retry_after = _retry_after_of(exc)
    if isinstance(exc, TimeoutError) or "timeout" in kind or status in (408, 504):
        return make_error("timeout")
    if status in (401, 403) or "authentication" in kind or "permissiondenied" in kind:
        return make_error("auth")
    if "insufficient_quota" in marker or "billing" in marker or status == 402:
        return make_error("quota")
    if status == 429 or "ratelimit" in kind or "resource_exhausted" in marker:
        return make_error("rate_limit", retry_after_s=retry_after)
    if status == 413 or "context_length" in marker or "too_long" in marker:
        return make_error("context_length")
    if status == 404:
        return make_error(
            "model_error", message="The model was not found.", fix_action="choose_model"
        )
    if status in (500, 502, 503, 529) or "overloaded" in marker or "overloaded" in kind:
        return make_error("overloaded", retry_after_s=retry_after)
    if any(word in kind for word in ("connect", "network", "remoteprotocol", "readerror")):
        return make_error("network")
    return make_error("model_error")


def map_sdk_error(exc: BaseException, provider: str = "") -> ModelError:
    """Classify an SDK failure for an adapter and log its type and status. Raise the result:
    ``raise map_sdk_error(exc, self.provider) from None``."""
    if not isinstance(exc, ModelError):
        log_model_error(exc, provider=provider)
    return classify_exception(exc)


def text_of(value: object) -> str:
    """The value when it already is text, ``""`` otherwise. Never ``str()`` of an SDK value: that
    can turn a missing reply into the text "None"."""
    return value if isinstance(value, str) else ""


# --- the policy: timeout, one retry, stop rules ----------------------------------------------


def _note_attempt() -> None:
    hook = ATTEMPT_HOOK.get()
    if hook is not None:
        hook()


def _retryable(err: ModelError) -> bool:
    if err.code not in ("network", "overloaded", "rate_limit"):
        return False
    return err.retry_after_s is None or err.retry_after_s <= RETRY_MAX_WAIT_S


async def run_with_policy(
    attempt: Callable[[], Awaitable[Completion]],
    *,
    deadline_s: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    provider: str = "",
) -> Completion:
    """Run one provider attempt under the shared policy.

    Each attempt runs under ``asyncio.timeout(min(ATTEMPT_CEILING_S, time left))`` and calls the
    attempt hook first. A ``network``, ``overloaded`` or ``rate_limit`` failure with a
    ``retry_after_s`` of 10 or less (or none) is retried once after that wait, if the deadline
    allows. ``clock`` and ``sleep`` are injectable so a test needs no real time.
    """
    started = clock()
    retried = False
    while True:
        remaining = deadline_s - (clock() - started)
        if remaining <= 0:
            raise make_error("timeout")
        _note_attempt()
        err: ModelError
        try:
            async with asyncio.timeout(min(ATTEMPT_CEILING_S, remaining)):
                return await attempt()
        except asyncio.CancelledError:
            raise
        except NotImplementedError:
            raise
        except Exception as exc:
            err = map_sdk_error(exc, provider)
        # Raised here, outside the handler, so the SDK exception (and the frames that held a
        # client) does not ride along as the error's context.
        wait = err.retry_after_s if err.retry_after_s is not None else 1.0
        if retried or not _retryable(err) or wait >= deadline_s - (clock() - started):
            raise err from None
        retried = True
        await sleep(wait)


async def complete_with_policy(
    attempt: Callable[[], Awaitable[Completion]],
    req: Request,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    provider: str = "",
) -> Completion:
    """``run_with_policy`` plus the stop rules for a whole request.

    A refusal or filter raises ``RefusedError`` (the text is never returned). An empty reply gets
    one retry, then ``EmptyReplyError``. A ``length`` stop is returned for the caller to judge.
    The deadline of ``req`` covers the empty-reply retry too.
    """
    started = clock()
    for round_ in (0, 1):
        remaining = req.deadline_s - (clock() - started)
        done = await run_with_policy(
            attempt, deadline_s=remaining, clock=clock, sleep=sleep, provider=provider
        )
        if done.stop in ("refusal", "filtered"):
            raise make_error("refused")
        if done.stop == "empty" or (done.stop == "end" and not done.text.strip()):
            if round_ == 0:
                continue
            raise EmptyReplyError(error_message("empty"), fix_action="retry")
        return done
    raise EmptyReplyError(error_message("empty"), fix_action="retry")  # unreachable


def check_stop(done: Completion, *, envelope: bool) -> Completion:
    """For a reply that must be a complete JSON object: a ``length`` stop raises
    ``TruncatedError``. Plain chat text may end at the limit (``envelope=False``)."""
    if done.stop == "length" and envelope:
        raise TruncatedError(error_message("truncated"), fix_action="retry")
    return done


def cap_visible_text(text: str, cap: int = VISIBLE_TEXT_CAP) -> str:
    """Cut text to ``cap`` characters, at a word boundary when one is near."""
    if len(text) <= cap:
        return text
    cut = text[:cap]
    space = cut.rfind(" ")
    if space > cap * 0.8:
        cut = cut[:space]
    return cut.rstrip()


# --- structured output ----------------------------------------------------------------------


def assert_no_numbers(schema: type[BaseModel]) -> None:
    """A ``structured()`` response schema must hold no numeric field (the model never produces a
    number). Raises ``TypeError``. Id patterns, recursion and optional fields stay allowed."""
    assert_structured_schema(schema.model_json_schema())


def first_json_object(raw: str) -> Any:
    """Parse the first JSON object in ``raw``: ``JSONDecoder().raw_decode`` from the first ``{``.

    Text after the object is ignored, and braces inside quoted strings cannot end it early.
    Raises ``ValueError`` when there is no object."""
    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object in the reply")
    value, _ = json.JSONDecoder().raw_decode(raw, start)
    return value


def _problems(exc: ValidationError | ValueError) -> str:
    """The validation problems as ``where: what``, without the offending input."""
    if isinstance(exc, ValidationError):
        lines = [
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}"
            for e in exc.errors(include_url=False, include_input=False)
        ]
        return "; ".join(lines)[:600]
    return str(exc)[:300]


async def structured_with_retry[T: BaseModel](
    port: ModelPortV2,
    system: str,
    text: str,
    schema: type[T],
    *,
    repairs: int = 2,
    principal: str | None = None,
    trace_id: str | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> T:
    """Prompted JSON, validated, with up to ``repairs`` repairs that feed the error back.

    The schema goes into the instructions. The first JSON object in the reply is parsed and
    validated against ``schema``. A reply that fails gets one more call that quotes the problems,
    up to ``repairs`` times, then ``BadOutputError``. A ``length`` stop raises ``TruncatedError``
    at once: a cut-off object cannot be repaired.
    """
    assert_no_numbers(schema)
    instructions = (
        system
        + "\nAnswer with one JSON object matching this schema, nothing else:\n"
        + json.dumps(schema.model_json_schema())
    )
    who = principal if principal is not None else CURRENT_PRINCIPAL.get()
    trace = trace_id or f"s{int(time.time() * 1000):x}"
    feedback = ""
    for _ in range(repairs + 1):
        done = await port.complete(
            Request(
                instructions=instructions,
                input=text + feedback,
                max_output=STRUCTURED_OUTPUT_TOKENS,
                deadline_s=deadline_s,
                principal=who,
                trace_id=trace,
            )
        )
        check_stop(done, envelope=True)
        try:
            return schema.model_validate(first_json_object(done.text))
        except (ValidationError, ValueError) as exc:
            feedback = (
                "\n\nYour previous answer was rejected: "
                + _problems(exc)
                + "\nAnswer again with one corrected JSON object only."
            )
    raise BadOutputError(error_message("bad_output"), fix_action="retry")


# --- ports ----------------------------------------------------------------------------------


class ModelPort(Protocol):
    """The old port, kept for callers that only need ``reply`` and ``structured``."""

    name: str

    async def reply(self, system: str, text: str) -> str: ...

    async def structured(self, system: str, text: str, schema: type[M]) -> M: ...


class PortShims:
    """``reply()`` and ``structured()`` over ``complete()``, the same for every provider."""

    provider: str = "hosted"
    model: str = ""

    async def complete(self, req: Request) -> Completion:
        raise NotImplementedError

    def principal(self) -> str:
        """The person a shim call is made and metered for."""
        return CURRENT_PRINCIPAL.get()

    def _request(self, instructions: str, text: str, max_output: int) -> Request:
        return Request(
            instructions=instructions,
            input=text,
            max_output=max_output,
            deadline_s=DEFAULT_DEADLINE_S,
            principal=self.principal(),
            trace_id=f"r{int(time.time() * 1000):x}",
        )

    async def reply(self, system: str, text: str) -> str:
        done = await self.complete(self._request(system, text, _OUTPUT_BUDGET.get()))
        return cap_visible_text(done.text)

    async def structured(self, system: str, text: str, schema: type[M]) -> M:
        return await structured_with_retry(self, system, text, schema, principal=self.principal())


_POOLS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, Any]] = (
    weakref.WeakKeyDictionary()
)


def client_timeout(deadline_s: float) -> float:
    """An SDK client timeout that ends before the request deadline does."""
    return max(deadline_s - 2.0, deadline_s * 0.5)


def shared_pool(provider: str, make: Callable[[], Any]) -> Any:
    """The HTTP connection pool one provider's calls share on this event loop. It holds no key;
    the SDK client that does is built for one call. ``make`` builds a pool on first use (or when
    the old one was closed)."""
    pools = _POOLS.setdefault(asyncio.get_running_loop(), {})
    pool = pools.get(provider)
    if pool is None or getattr(pool, "is_closed", False):
        pool = make()
        pools[provider] = pool
    return pool


class _HTTPModel(PortShims):
    """Shared shape for hosted providers. Built only when the owner has a key.

    ``key`` is the key itself, or a callable that returns the key for each call (``None`` once it
    is gone). The resolver passes a callable, so a key removed at sign-out stops working at the
    next call, with nothing cached.
    """

    name = "hosted"

    def __init__(self, key: str | Callable[[], str | None], model: str) -> None:
        if callable(key):
            self._key_source: Callable[[], str | None] = key
        else:
            if not key:
                raise NotConfigured(f"{self.name}: no key for this agent's owner")
            fixed = key
            self._key_source = lambda: fixed
        self.provider = self.name
        self.model = model

    @property
    def _key(self) -> str:
        return self.current_key()

    def current_key(self) -> str:
        """The key for this call. ``NotConfigured`` when it is gone."""
        key = self._key_source()
        if not key:
            raise NotConfigured(
                f"{self.name}: no key for this agent's owner",
                user_message=error_message("not_configured"),
                fix_action="reenter_key",
            )
        return key

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.model!r}, key=[redacted])"

    async def _attempt(self, req: Request) -> Completion:
        """One provider request. Adapters implement this. The default calls an old-style
        ``reply`` override once, so an adapter written for the old port keeps working."""
        if type(self).reply is PortShims.reply:
            raise NotImplementedError(f"{type(self).__name__} must implement _attempt")
        token = _OUTPUT_BUDGET.set(req.max_output)
        try:
            raw = await self.reply(req.instructions, req.input)
        finally:
            _OUTPUT_BUDGET.reset(token)
        return Completion(text=text_of(raw), stop="end", model_used=self.model)

    async def call_with_client(
        self,
        req: Request,
        make: Callable[[str, float], Any],
        send: Callable[[Any], Awaitable[Any]],
        refine: Callable[[Exception], ModelError | None] | None = None,
    ) -> Any:
        """Build an SDK client for this one call, run ``send(client)`` and drop the client.

        The key comes from ``current_key()``. A failure becomes a fixed ``ModelError``: ``refine``
        may classify what only the provider knows, ``map_sdk_error`` does the rest. The error is
        raised after the handler, so no SDK exception or message rides along, and the client that
        holds the key is dropped even while a traceback is alive."""
        client: Any = make(self.current_key(), client_timeout(req.deadline_s))
        failure: ModelError | None = None
        reply: Any = None
        try:
            reply = await send(client)
        except Exception as exc:
            refined = refine(exc) if refine is not None else None
            if refined is not None:
                log_model_error(exc, provider=self.provider)
            failure = refined or map_sdk_error(exc, self.provider)
        finally:
            client = None
        if failure is not None:
            raise failure
        return reply

    async def complete(self, req: Request) -> Completion:
        return await complete_with_policy(lambda: self._attempt(req), req, provider=self.provider)
