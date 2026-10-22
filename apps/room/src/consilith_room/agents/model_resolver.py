"""Which model an agent runs on, and whether it can run now (section 3, "Credentials").

``ModelResolver.for_principal(room, aid)`` returns ``Ready(model)`` or ``Unavailable(code, reason,
fix_action)`` and never raises. One readiness rule serves chat, the composer, dispatch and peer
turns, so they cannot disagree:

* a key provider is ready when some live session of the agent's owner holds a key for it;
* ChatGPT is ready when the helper is connected by that owner, reports a signed-in plan, and the
  owner has a live session.

A ``Ready`` model is bound to the agent's owner. Every call it makes carries that person as the
principal, uses only that person's credential (looked up again at each call, never cached), and
fails with ``not_configured`` the moment the key or the sign-in is gone. A call for any other
person is refused before it reaches a provider. Calls are metered per person (``meter.py``).

Nothing here echoes a canned answer for a missing model. The one remaining echo is the deprecated
``resolve_model`` shim, which the kernel still calls until it uses ``for_principal``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..domain.errors import RoomError
from ..domain.prefs import DEFAULT_MODELS, ModelTable, hourly_cap_of, model_table
from ..domain.prefs import validate_agent_model as _validate_pair
from ..domain.service import RoomService
from ..platform.errors import ModelError, NotConfigured
from ..platform.keys import SessionKeys
from ..platform.siwc_client import SiwcClient
from ..platform.store import Store
from .meter import Meter
from .model_types import Completion, ModelPortV2, Request
from .providers import adapter_for
from .providers.base import CURRENT_PRINCIPAL, PortShims, error_message
from .providers.scripted import ScriptedModel

log = logging.getLogger("consilith.models")

# Providers that take a key, as the registry says. Anything else is not looked up in ``keys``.
_FIX_REENTER = "reenter_key"


@dataclass(frozen=True)
class Ready:
    """The agent can run. ``model`` is bound to the agent's owner and metered."""

    model: ModelPortV2


@dataclass(frozen=True)
class Unavailable:
    """The agent cannot run now. ``code`` is a ``MODEL_ERROR_CODES`` value (usually
    ``not_configured``), ``reason`` is the words for the person, ``fix_action`` is what the
    interface can offer (``reenter_key``, ``reconnect``, ``choose_model``, ...)."""

    code: str
    reason: str
    fix_action: str | None = None

    def error(self) -> NotConfigured:
        """The same facts as an exception, for a route that must answer with one."""
        return NotConfigured(self.reason, user_message=self.reason, fix_action=self.fix_action)


Resolution = Ready | Unavailable


def meter_for(store: Store, **kwargs: Any) -> Meter:
    """A meter whose per-person cap comes from the caps people set in their prefs."""
    return Meter(cap_of=lambda person: hourly_cap_of(store.agent_prefs(person)), **kwargs)


class _BoundPort(PortShims):
    """A model bound to one owner: the per-run credential guard, the output clamp and the
    credential re-check all happen here, before the metered port is reached."""

    def __init__(
        self,
        inner: ModelPortV2,
        *,
        owner: str,
        aid: str,
        provider: str,
        model: str,
        check: Callable[[], str | None],
        table: Callable[[], ModelTable],
    ) -> None:
        self._inner = inner
        self._owner = owner
        self._aid = aid
        self._check = check
        self._table = table
        self.provider = provider
        self.model = model
        self.name = provider  # the old port's attribute, still read by callers

    def __repr__(self) -> str:
        return f"BoundModel({self.provider}, {self.model}, agent={self._aid})"

    def principal(self) -> str:
        return self._owner

    async def complete(self, req: Request) -> Completion:
        if req.principal != self._owner:
            # Agent X's model only ever spends X's owner's credential.
            raise NotConfigured(
                "this model connection belongs to another person",
                user_message=error_message("not_configured"),
                fix_action=None,
            )
        gone = self._check()
        if gone is not None:
            raise NotConfigured(gone, user_message=gone, fix_action=_fix_for(self.provider))
        wanted = self._table().clamp_output(self.provider, self.model, req.max_output)
        if wanted != req.max_output:
            req = req.model_copy(update={"max_output": wanted})
        token = CURRENT_PRINCIPAL.set(self._owner)
        try:
            return await self._inner.complete(req)
        finally:
            CURRENT_PRINCIPAL.reset(token)


def _fix_for(provider: str) -> str:
    return "reconnect" if provider == "siwc" else _FIX_REENTER


def _label(provider: str) -> str:
    from consilith_contracts import PROVIDERS

    descriptor = PROVIDERS.get(provider)
    return descriptor.label if descriptor is not None else provider


def _default_siwc_for(person: str) -> SiwcClient | None:
    from . import models as _models

    client = _models.SIWC
    if client is not None and client.available and client.owner == person:
        return client
    return None


class ModelResolver:
    """Resolves an agent to a model. Build one per process, hand it the shared meter."""

    def __init__(
        self,
        keys: SessionKeys | None,
        *,
        meter: Meter | None = None,
        siwc_for: Callable[[str], SiwcClient | None] | None = None,
        table: Callable[[], ModelTable] = model_table,
    ) -> None:
        self._keys = keys
        self._meter = meter
        self._siwc_for = siwc_for or _default_siwc_for
        self._table = table

    # readiness ---------------------------------------------------------------------------------
    def _key_ready(self, person: str, provider: str) -> bool:
        return self._keys is not None and self._keys.ready(person, provider)

    def _siwc_live(self, person: str) -> SiwcClient | None:
        client = self._siwc_for(person)
        if client is None or self._keys is None or not self._keys.has_live_session(person):
            return None
        return client

    async def credential_ready(self, person: str, provider: str) -> bool:
        """Whether ``person`` can run a model on ``provider`` right now. Never raises."""
        try:
            if provider == "scripted":
                return True
            if provider == "siwc":
                client = self._siwc_live(person)
                if client is None:
                    return False
                status = await client.call("GET", "/status")
                return bool(isinstance(status, dict) and status.get("signedIn"))  # pyright: ignore[reportUnknownMemberType]
            return self._key_ready(person, provider)
        except Exception as exc:
            log.warning("readiness check failed: type=%s", type(exc).__name__)
            return False

    # resolving ---------------------------------------------------------------------------------
    def resolve_now(self, room: RoomService, aid: str) -> Resolution:
        """Everything ``for_principal`` checks except the ChatGPT helper's live status (which
        needs a network call). Never raises."""
        try:
            return self._resolve(room, aid)
        except Exception as exc:  # a broken room document must not become a 500 in a route
            log.warning("model resolution failed: type=%s", type(exc).__name__)
            return Unavailable("not_configured", error_message("not_configured"), "connect")

    async def for_principal(self, room: RoomService, aid: str) -> Resolution:
        """``Ready(model)`` or ``Unavailable(code, reason, fix_action)``. Never raises."""
        first = self.resolve_now(room, aid)
        if isinstance(first, Unavailable):
            return first
        try:
            cfg = room.agent_config(aid)
            owner = str(room.agent(aid)["owner"])
        except Exception:
            return Unavailable("not_configured", error_message("not_configured"), "connect")
        if cfg.model_provider == "siwc" and not await self.credential_ready(owner, "siwc"):
            return Unavailable(
                "not_configured",
                "Your ChatGPT plan is not signed in. Connect it again in Your agent.",
                "reconnect",
            )
        return first

    def _resolve(self, room: RoomService, aid: str) -> Resolution:
        agent = room.agent(aid)
        if agent.get("brought"):
            return Unavailable(
                "not_configured", "A brought agent answers with its own model.", None
            )
        cfg = room.agent_config(aid)
        owner = str(agent["owner"])
        provider = cfg.model_provider
        if provider == "scripted":
            return Ready(ScriptedModel())
        label = _label(provider)
        model = cfg.model_name or DEFAULT_MODELS.get(provider)
        if not model:
            return Unavailable(
                "not_configured", f"Choose a {label} model in Your agent.", "choose_model"
            )
        try:
            _validate_pair(provider, model, self._table())
        except RoomError as exc:
            return Unavailable("not_configured", exc.message, "choose_model")
        if provider == "siwc":
            client = self._siwc_live(owner)
            if client is None:
                return Unavailable(
                    "not_configured",
                    "Your ChatGPT plan is not connected here. Connect it in Your agent.",
                    "reconnect",
                )
            return self._bind(
                adapter_for("siwc")(client, model),
                owner,
                aid,
                provider,
                model,
                lambda: (
                    None
                    if self._siwc_live(owner) is client
                    else "Your ChatGPT plan is no longer connected. Connect it again in Your agent."
                ),
            )
        keys = self._keys
        if keys is None or not keys.ready(owner, provider):
            return Unavailable(
                "not_configured",
                f"Your {label} key is not in this session. Enter it again in Your agent.",
                _FIX_REENTER,
            )

        def key() -> str | None:
            return keys.for_run(owner, provider)

        return self._bind(
            adapter_for(provider)(key, model),
            owner,
            aid,
            provider,
            model,
            lambda: (
                None
                if keys.ready(owner, provider)
                else f"Your {label} key is no longer held. Enter it again in Your agent."
            ),
        )

    def _bind(
        self,
        adapter: Any,
        owner: str,
        aid: str,
        provider: str,
        model: str,
        check: Callable[[], str | None],
    ) -> Ready:
        inner: ModelPortV2 = adapter
        if self._meter is not None:
            inner = self._meter.wrap(inner)
        return Ready(
            _BoundPort(
                inner,
                owner=owner,
                aid=aid,
                provider=provider,
                model=model,
                check=check,
                table=self._table,
            )
        )


def resolve_model(
    keys: SessionKeys | None,
    room: RoomService,
    aid: str,
    meter: Meter | None = None,
    *,
    resolver: ModelResolver | None = None,
) -> Any:
    """Deprecated shim for the kernel: a port, with a scripted stand-in (and its note) when the
    agent cannot run. New callers use ``ModelResolver.for_principal`` and show ``Unavailable``.
    A given ``resolver`` is used as is, so its meter is the one every call is counted on."""
    resolved = (resolver or ModelResolver(keys, meter=meter)).resolve_now(room, aid)
    if isinstance(resolved, Ready):
        return resolved.model
    return ScriptedModel(note=resolved.reason)


__all__ = [
    "ModelError",
    "ModelResolver",
    "Ready",
    "Resolution",
    "Unavailable",
    "meter_for",
    "resolve_model",
]
