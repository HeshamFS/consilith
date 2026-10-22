"""One meter around every model adapter (section 3, decision 17).

``Meter.wrap(port)`` returns a port that:

* counts one model call per provider attempt, per person, including every repair and every retry
  (the shared policy in ``providers/base.py`` calls the attempt hook before each attempt);
* refuses an attempt when the person has used their hourly cap (60 by default; the person can set
  it in their prefs, up to the operator's ceiling) with a ``quota`` error that says when it
  resets and is never retried;
* lets one model call run at a time per person, whatever the provider;
* collects the optional token usage a provider reports, and applies the optional hourly token cap
  to providers that report tokens.

The scripted model is free and is not counted. The unit shown to people is "model calls", the one
measure all four providers share.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.prefs import HOURLY_CAP_CEILING, HOURLY_CAP_DEFAULT, effective_hourly_cap
from ..platform.errors import ModelError, NotConfigured, QuotaError
from .model_types import Completion, ModelPortV2, Request
from .providers.base import ATTEMPT_HOOK, PortShims

_HOUR = 3600.0


@dataclass(frozen=True)
class MeterStats:
    """What a person has used in the last hour."""

    calls: int
    cap: int
    input_tokens: int
    output_tokens: int

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.calls)


class Meter:
    """Counts model calls per person. One instance serves the whole process."""

    def __init__(
        self,
        *,
        default_cap: int = HOURLY_CAP_DEFAULT,
        ceiling: int = HOURLY_CAP_CEILING,
        cap_of: Callable[[str], int | None] | None = None,
        token_cap: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._default_cap = default_cap
        self._ceiling = ceiling
        self._cap_of = cap_of
        self._token_cap = token_cap
        self._clock = clock
        self._calls: dict[str, deque[float]] = {}
        self._tokens: dict[str, deque[tuple[float, int, int]]] = {}
        self._locks: dict[str, _PersonLock] = {}

    # the numbers -------------------------------------------------------------------------------
    def cap(self, person: str) -> int:
        """The hourly cap for this person: their own setting (or the default), within the
        operator's ceiling."""
        chosen = self._cap_of(person) if self._cap_of is not None else None
        return effective_hourly_cap(
            chosen if chosen is not None else self._default_cap, self._ceiling
        )

    def _trim(self, person: str) -> deque[float]:
        window = self._calls.setdefault(person, deque())
        floor = self._clock() - _HOUR
        while window and window[0] <= floor:
            window.popleft()
        return window

    def _trim_tokens(self, person: str) -> deque[tuple[float, int, int]]:
        window = self._tokens.setdefault(person, deque())
        floor = self._clock() - _HOUR
        while window and window[0][0] <= floor:
            window.popleft()
        return window

    def stats(self, person: str) -> MeterStats:
        used = self._trim_tokens(person)
        return MeterStats(
            calls=len(self._trim(person)),
            cap=self.cap(person),
            input_tokens=sum(t[1] for t in used),
            output_tokens=sum(t[2] for t in used),
        )

    def charge(self, person: str) -> None:
        """Count one attempt for ``person``, or raise ``QuotaError`` when the cap is used up."""
        window = self._trim(person)
        cap = self.cap(person)
        if len(window) >= cap:
            wait = max(1.0, window[0] + _HOUR - self._clock())
            raise QuotaError(
                f"hourly limit of {cap} model calls reached",
                user_message=(
                    f"You have used your {cap} model calls for this hour. "
                    "Older calls free up as the hour passes."
                ),
                fix_action="wait",
                retry_after_s=wait,
            )
        window.append(self._clock())

    def record_usage(
        self, person: str, input_tokens: int | None, output_tokens: int | None
    ) -> None:
        if input_tokens is None and output_tokens is None:
            return
        self._trim_tokens(person).append((self._clock(), input_tokens or 0, output_tokens or 0))

    def _check_tokens(self, person: str) -> None:
        if self._token_cap is None:
            return
        used = sum(t[1] + t[2] for t in self._trim_tokens(person))
        if used >= self._token_cap:
            raise QuotaError(
                "hourly token limit reached",
                user_message="You have used your token allowance for this hour.",
                fix_action="wait",
            )

    # wrapping ----------------------------------------------------------------------------------
    def wrap(self, port: ModelPortV2) -> ModelPortV2:
        """A port that meters ``port``. The scripted model is returned as is: it costs nothing."""
        if port.provider == "scripted":
            return port
        return _MeteredPort(self, port)

    def lock_for(self, person: str) -> _PersonLock:
        return self._locks.setdefault(person, _PersonLock())


class _PersonLock:
    """One call at a time for a person. Created lazily, so it binds to the running loop."""

    def __init__(self) -> None:
        import asyncio

        self._lock = asyncio.Lock()

    async def __aenter__(self) -> None:
        await self._lock.acquire()

    async def __aexit__(self, *_exc: object) -> None:
        self._lock.release()


class _MeteredPort(PortShims):
    def __init__(self, meter: Meter, inner: ModelPortV2) -> None:
        self._meter = meter
        self._inner = inner
        self.provider = inner.provider
        self.model = inner.model

    def __repr__(self) -> str:
        return f"Metered({self._inner!r})"

    async def complete(self, req: Request) -> Completion:
        person = req.principal
        meter = self._meter
        async with meter.lock_for(person):
            meter._check_tokens(person)  # pyright: ignore[reportPrivateUsage]
            counted = [0]

            def hook() -> None:
                meter.charge(person)
                counted[0] += 1

            token = ATTEMPT_HOOK.set(hook)
            try:
                done = await self._inner.complete(req)
            except NotConfigured:
                raise
            except ModelError:
                if counted[0] == 0:
                    meter.charge(person)  # a port without the hook still made an attempt
                raise
            finally:
                ATTEMPT_HOOK.reset(token)
            if counted[0] == 0:
                meter.charge(person)
            if done.usage is not None:
                meter.record_usage(person, done.usage.input_tokens, done.usage.output_tokens)
            return done
