"""Your agent: the choice made once (provider, model, autonomy, brought card) and the session key.

A key lives in server memory for the signed-in session that put it. It is never returned, stored,
journaled or logged (platform/keys.py).
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

from consilith_contracts import PROVIDERS
from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel, SecretStr

from ..agents import models as models_mod
from ..agents.model_types import Request
from ..agents.models import NotConfigured, list_models
from ..agents.providers import adapter_for
from ..domain.errors import Conflict, Denied, RoomError
from ..domain.prefs import DEFAULT_MODELS, AgentPrefs, default_prefs, model_table
from ..domain.roles import autonomy_for_existing_seat
from ..platform.errors import AuthError, EmptyReplyError, ModelError, RefusedError
from ..platform.siwc_client import (
    DEFAULT_AUTH_ORIGINS,
    LEAVE_OFFERS,
    SiwcAccounts,
    SiwcClient,
    leave_offer_pending,
)
from ..protocols.outbound import OutboundPolicy, OutboundRefused
from .auth import Session
from .hub import Hub
from .schemas import KeyIn

log = logging.getLogger("consilith.me")

# What each way of connecting means for how long it lasts, in one line the Connect panel shows.
_KEY_LIFETIME = (
    "Held in server memory for your signed-in sessions (up to {hours} hours). "
    "Gone when you sign out or the server restarts."
)
_CHATGPT_LIFETIME = (
    "Stays signed in on this host until you disconnect it. "
    "Your agent also needs you signed in here."
)


class SiwcLoginIn(BaseModel):
    """``switchAccount`` signs a different ChatGPT account into this person's slot."""

    switchAccount: bool = False


class SiwcCallbackIn(BaseModel):
    """The address the browser ended on after ChatGPT sign-in. A secret: never echoed or logged."""

    callbackUrl: SecretStr


# A status error with one of these codes means the saved sign-in no longer works.
_REAUTH_CODES = frozenset(
    {
        "not_signed_in",
        "sign_in_required",
        "reauth_required",
        "invalid_grant",
        "invalid_refresh_token",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)


def siwc_accounts(hub: Hub) -> SiwcAccounts:
    """The ChatGPT lookup for this server, following the ``CONSILITH_SIWC_MULTI`` operator flag."""
    client = models_mod.SIWC
    if client is not None:
        client.multi = hub.settings.siwc_multi
    return SiwcAccounts(client)


def mask_email(email: str | None) -> str | None:
    """``ab@example.org`` -> ``a***@example.org``: enough for a person to recognise their own
    account and to see when a private window picked up the wrong one."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def _bridge_problem(exc: NotConfigured) -> RoomError:
    """A bridge refusal as the room's own error, with the bridge's fixed words as the reason."""
    code = str(exc.code)
    if code in {"sign_in_busy", "sign_in_rate_limited", "already_signed_in", "no_sign_in"}:
        return Conflict(exc.user_message, code=code)
    return RoomError(exc.user_message, code=code)


def _auth_origins(caps: dict[str, Any]) -> list[str]:
    sign_in = caps.get("signIn")
    declared: Any = sign_in.get("authOrigins") if isinstance(sign_in, dict) else None  # pyright: ignore[reportUnknownMemberType]
    if isinstance(declared, list):
        listed = [o for o in declared if isinstance(o, str)]  # pyright: ignore[reportUnknownVariableType]
        if listed:
            return listed
    return list(DEFAULT_AUTH_ORIGINS)


def check_sign_in_url(url: object, origins: list[str]) -> str:
    """The sign-in address must be https on an origin the bridge declared. The page opens it."""
    if not isinstance(url, str):
        raise RoomError(
            "The ChatGPT helper did not return a sign-in address.", code="bad_login_url"
        )
    parts = urlsplit(url)
    if parts.scheme != "https" or f"https://{parts.netloc}" not in origins:
        raise RoomError("The ChatGPT sign-in address was not trusted.", code="bad_login_url")
    return url


def prefs_of(hub: Hub, pid: str) -> AgentPrefs:
    stored = hub.store.agent_prefs(pid)
    if stored is not None:
        return AgentPrefs.model_validate(stored)
    return default_prefs(str(hub.accounts.require(pid)["discipline"]))


async def _siwc_credential_multi(client: SiwcClient) -> dict[str, Any]:
    """One person's own ChatGPT account on the bridge: a state and the bridge's reason."""
    base: dict[str, Any] = {"lifetime": _CHATGPT_LIFETIME}
    try:
        status = await client.call("GET", "/status")
    except NotConfigured as exc:
        return {**base, "state": "not_connected", "reason": exc.user_message}
    if status.get("signedIn"):
        return {**base, "state": "ready"}
    if status.get("signingIn"):
        return {**base, "state": "validating"}
    error = status.get("error")
    if isinstance(error, dict):
        code = error.get("code")  # pyright: ignore[reportUnknownMemberType]
        message = error.get("message")  # pyright: ignore[reportUnknownMemberType]
        state = "expired" if code in _REAUTH_CODES else "not_connected"
        return {**base, "state": state, "reason": message or "Sign in with ChatGPT again."}
    return {**base, "state": "not_connected"}


async def _siwc_credential(hub: Hub, person: str) -> dict[str, Any]:
    """The ChatGPT connection as the Connect panel shows it: a state and the server's reason."""
    base: dict[str, Any] = {"lifetime": _CHATGPT_LIFETIME}
    client = models_mod.SIWC
    if client is None or not client.available:
        return {
            **base,
            "state": "not_connected",
            "reason": "Sign in with ChatGPT is not set up here.",
        }
    if hub.settings.siwc_multi:
        account = siwc_accounts(hub).for_person(person)
        if account is None:
            return {**base, "state": "not_connected"}
        return await _siwc_credential_multi(account)
    if client.owner not in (None, person):
        return {**base, "state": "not_connected", "reason": "connected by another person"}
    try:
        status = await client.call("GET", "/status")
    except NotConfigured as exc:
        return {**base, "state": "not_connected", "reason": exc.user_message}
    if status.get("signedIn") and client.owner == person:
        return {**base, "state": "ready"}
    if status.get("signingIn"):
        return {**base, "state": "validating"}
    if client.owner == person:
        error = status.get("error")
        reason = error.get("message") if isinstance(error, dict) else None
        return {**base, "state": "expired", "reason": reason or "Sign in with ChatGPT again."}
    return {**base, "state": "not_connected"}


async def credential_view(hub: Hub, session: Session, prefs: AgentPrefs) -> dict[str, Any]:
    """The state of every way this person can connect a model, and a lifetime line for each.

    ``state`` is ``not_connected``, ``ready``, ``expired`` (a ChatGPT sign-in that ended),
    ``needs_reentry`` (a key provider the person chose whose key is no longer held, for example
    after a restart) or ``validating`` (a ChatGPT sign-in in progress). The panel adds
    ``validating`` and ``invalid`` itself while it saves a key.
    """
    hours = max(1, hub.settings.token_ttl_s // 3600)
    by_provider: dict[str, dict[str, Any]] = {}
    for descriptor in PROVIDERS.values():
        if descriptor.auth == "key":
            if hub.keys.ready(session.person, descriptor.id):
                state = "ready"
            elif prefs.provider == descriptor.id:
                state = "needs_reentry"
            else:
                state = "not_connected"
            by_provider[descriptor.id] = {
                "state": state,
                "lifetime": _KEY_LIFETIME.format(hours=hours),
            }
        elif descriptor.auth == "chatgpt":
            by_provider[descriptor.id] = await _siwc_credential(hub, session.person)
    current = by_provider.get(prefs.provider)
    return {
        "provider": prefs.provider,
        "state": current["state"] if current else "ready",
        "lifetime": current["lifetime"] if current else "",
        "reason": current.get("reason") if current else None,
        "byProvider": by_provider,
    }


async def agent_view(hub: Hub, session: Session) -> dict[str, Any]:
    prefs = prefs_of(hub, session.person)
    model = prefs.model or DEFAULT_MODELS.get(prefs.provider)
    return {
        "kind": prefs.kind,
        "provider": prefs.provider,
        "model": model if prefs.kind == "consilith" else None,
        "autonomy": prefs.autonomy.value,
        "cardUrl": prefs.card_url,
        "hasKey": hub.keys.has(session.person, session.sid, prefs.provider),
        "hourlyCap": prefs.hourly_cap,
        "credential": await credential_view(hub, session, prefs),
    }


def _probe_enabled(hub: Hub) -> bool:
    """Saving a key checks it with the provider. The test environment skips this unless a test
    turns it on (``hub.force_key_probe``), because it would call the network."""
    return bool(getattr(hub, "force_key_probe", False)) or hub.settings.env != "test"


async def validate_key(hub: Hub, person: str, provider: str, key: str) -> None:
    """Check a key before it is kept: list its models, then make one tiny ``complete()`` call.

    A key the provider rejects is a 400 ``key_invalid`` with fixed words. Any other model failure
    keeps its own code (rate limit, network, ...). The key is never logged or echoed.
    """
    try:
        models = await list_models(provider, key, person)
        stored = prefs_of(hub, person)
        listed = [m["id"] for m in models]
        verified = [r.model for r in model_table().for_provider(provider) if r.model in listed]
        if stored.provider == provider and stored.model:
            chosen = stored.model
        elif verified:
            chosen = verified[0]
        elif listed:
            chosen = listed[0]
        else:
            raise RoomError("This key lists no chat models.", code="key_invalid")
        probe = adapter_for(provider)(key, chosen)
        with contextlib.suppress(EmptyReplyError, RefusedError):  # the provider answered
            await probe.complete(
                Request(
                    instructions="Reply with the single word ok.",
                    input="ok",
                    max_output=64,
                    deadline_s=30.0,
                    principal=person,
                    trace_id="key-probe",
                )
            )
    except AuthError as exc:
        log.warning("key probe refused: provider=%s code=%s", provider, exc.code)
        raise RoomError(exc.user_message, code="key_invalid") from None
    except ModelError as exc:
        log.warning("key probe failed: provider=%s code=%s", provider, exc.code)
        raise


async def check_card_url(policy: OutboundPolicy, card_url: str) -> None:
    """A card URL passes the brought-agent outbound policy before it is stored or used."""
    try:
        policy.check_url(card_url, card=True)
        parts = urlsplit(card_url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        await asyncio.to_thread(policy.resolve, parts.hostname or "", port)
    except OutboundRefused as exc:
        raise Denied(f"agent card refused: {exc}") from exc


async def _siwc_models(hub: Hub, person: str) -> list[dict[str, str]]:
    """The models this person's own ChatGPT plan lists, through their account on the bridge."""
    account = siwc_accounts(hub).for_person(person)
    if account is None:
        raise NotConfigured("Sign in with ChatGPT is not set up on this server")
    try:
        listed = await account.call("GET", "/models")
    except NotConfigured as exc:
        if exc.code in _REAUTH_CODES:
            raise Conflict("connect your ChatGPT plan first") from None
        raise
    return [
        {"id": str(m["id"]), "label": str(m.get("label") or m["id"])}
        for m in listed.get("models", [])
    ]


def me_agent_router(hub: Hub, current: Callable[..., Session]) -> APIRouter:
    router = APIRouter()

    @router.get("/me/agent")
    async def get_agent(session: Annotated[Session, Depends(current)]) -> dict[str, Any]:
        return await agent_view(hub, session)

    @router.put("/me/agent")
    async def put_agent(
        body: AgentPrefs, session: Annotated[Session, Depends(current)]
    ) -> dict[str, Any]:
        stored = hub.store.agent_prefs(session.person)
        if stored is not None:
            before = AgentPrefs.model_validate(stored)
            if before.provider != body.provider and before.model and body.model == before.model:
                # Changing the provider clears the model: the old id belongs to the old maker.
                body = body.model_copy(update={"model": None})
        problem = body.problems()
        if problem is not None:
            raise RoomError(problem)
        if body.card_url is not None:
            await check_card_url(hub.runtime.brought.policy, body.card_url)
        hub.store.put_agent_prefs(session.person, body.wire())
        _apply_to_seats(hub, session.person, body)
        return await agent_view(hub, session)

    @router.put("/me/agent/key", status_code=204)
    async def put_key(body: KeyIn, session: Annotated[Session, Depends(current)]) -> Response:
        key = body.key.get_secret_value()
        if _probe_enabled(hub):
            await validate_key(hub, session.person, body.provider, key)
        hub.keys.put(session.person, session.sid, body.provider, key)
        return Response(status_code=204)

    @router.get("/me/agent/models")
    async def get_models(
        provider: str, session: Annotated[Session, Depends(current)]
    ) -> dict[str, Any]:
        """The models the key this session holds for ``provider`` can use, newest first. Models
        in the operator's table come first, and the rest are marked unverified."""
        descriptor = PROVIDERS.get(provider)
        if descriptor is None or descriptor.auth not in ("key", "chatgpt"):
            listed = ", ".join(p.id for p in PROVIDERS.values() if p.auth in ("key", "chatgpt"))
            raise RoomError(f"models are listed for {listed}")
        multi_siwc = provider == "siwc" and hub.settings.siwc_multi
        if (
            provider == "siwc"
            and not multi_siwc
            and (models_mod.SIWC is None or models_mod.SIWC.owner != session.person)
        ):
            raise Conflict("connect your ChatGPT plan first")
        # This session's own key first, else the one readiness rule (any live session of the
        # same person), so the model list agrees with the "Ready" the key card shows.
        key = (
            ""
            if provider == "siwc"
            else hub.keys.for_session(session.person, session.sid, provider)
            or hub.keys.for_background(session.person, provider)
        )
        if key is None:
            raise Conflict("add your API key for this provider first")
        try:
            if multi_siwc:
                models = await _siwc_models(hub, session.person)
            else:
                models = await list_models(provider, key, session.person)
        except ModelError as exc:
            raise RoomError(exc.user_message) from None
        table = model_table()
        marked = [{**m, "verified": table.verified(provider, m["id"])} for m in models]
        marked.sort(key=lambda m: not m["verified"])  # stable: the provider's order within a group
        return {"provider": provider, "models": marked}

    # Sign in with ChatGPT, through the local sidecar (docs/siwc.md). The ChatGPT session lives
    # on this machine in the sidecar; the room never sees its tokens.
    @router.get("/me/agent/siwc")
    async def siwc_status(session: Annotated[Session, Depends(current)]) -> dict[str, Any]:
        client = models_mod.SIWC
        if client is None or not client.available:
            return {"available": False, "signedIn": False}
        if hub.settings.siwc_multi:
            return await _siwc_status_multi(hub, session.person)
        if client.owner not in (None, session.person):
            # Connected by someone else: nothing about that account is shown.
            return {"available": False, "signedIn": False, "reason": "connected by another person"}
        try:
            status = await client.call("GET", "/status")
        except NotConfigured as exc:
            return {"available": False, "signedIn": False, "reason": str(exc)}
        signed_in = bool(status.get("signedIn"))
        if client.owner is None and (signed_in or status.get("signingIn")):
            return {
                "available": False,
                "signedIn": False,
                "reason": (
                    "ChatGPT owner binding is missing. The local operator must disconnect "
                    "the helper before reconnecting."
                ),
            }
        if not signed_in and not status.get("signingIn") and client.owner == session.person:
            client.set_owner(None)  # the helper lost the sign-in; it is free again
        out: dict[str, Any] = {
            "available": True,
            "signedIn": signed_in,
            "signingIn": bool(status.get("signingIn")),
            "email": (status.get("account") or {}).get("email") if signed_in else None,
            "error": status.get("error"),
        }
        if leave_offer_pending(hub.store, session.person) and signed_in:
            out["offerDisconnect"] = True
        return out

    @router.post("/me/agent/siwc/login")
    async def siwc_login(
        session: Annotated[Session, Depends(current)],
        body: Annotated[SiwcLoginIn | None, Body()] = None,
    ) -> dict[str, Any]:
        client = models_mod.SIWC
        if client is None or not client.available:
            raise Conflict("Sign in with ChatGPT is not set up on this server")
        if hub.settings.siwc_multi:
            return await _siwc_login_multi(
                hub, session.person, switch=bool(body and body.switchAccount)
            )
        if client.owner not in (None, session.person):
            raise Denied("this machine's ChatGPT helper is connected by another person")
        if client.owner is None:
            try:
                status = await client.call("GET", "/status")
            except NotConfigured as exc:
                raise RoomError(str(exc)) from None
            if status.get("signedIn") or status.get("signingIn"):
                raise Denied(
                    "ChatGPT owner binding is missing; disconnect the helper locally first"
                )
            # Another account may have claimed it while we awaited the helper.
            if client.owner not in (None, session.person):
                raise Denied("this machine's ChatGPT helper is connected by another person")
        before = client.owner
        client.set_owner(session.person)
        try:
            out = await client.call("POST", "/login")
        except NotConfigured as exc:
            client.set_owner(before)
            raise RoomError(str(exc)) from None
        return {"url": out.get("url")}

    @router.post("/me/agent/siwc/logout")
    async def siwc_logout(session: Annotated[Session, Depends(current)]) -> dict[str, Any]:
        client = models_mod.SIWC
        if client is None or not client.available:
            return {"signedIn": False}
        if hub.settings.siwc_multi:
            account = siwc_accounts(hub).for_person(session.person)
            if account is None:
                return {"signedIn": False}
            try:
                await account.call("POST", "/logout")
            except NotConfigured as exc:
                raise _bridge_problem(exc) from None
            LEAVE_OFFERS.discard(session.person)
            return {"signedIn": False}
        if client.owner != session.person:
            raise Denied("only the person who connected this ChatGPT plan can disconnect it")
        try:
            await client.call("POST", "/logout")
        except NotConfigured as exc:
            raise RoomError(str(exc)) from None
        client.set_owner(None)
        LEAVE_OFFERS.discard(session.person)
        return {"signedIn": False}

    @router.post("/me/agent/siwc/callback")
    async def siwc_callback(
        body: SiwcCallbackIn, session: Annotated[Session, Depends(current)]
    ) -> dict[str, Any]:
        """Finish a ChatGPT sign-in from the address the browser ended on, for a person whose
        browser is not on the host. The address is a secret: it travels in the body and is never
        logged, echoed or kept."""
        if not hub.settings.siwc_multi:
            raise Conflict("pasting the sign-in address needs several ChatGPT accounts turned on")
        account = siwc_accounts(hub).for_person(session.person)
        if account is None:
            raise Conflict("Sign in with ChatGPT is not set up on this server")
        try:
            return await account.call(
                "POST", "/callback", {"callbackUrl": body.callbackUrl.get_secret_value()}
            )
        except NotConfigured as exc:
            raise _bridge_problem(exc) from None

    @router.delete("/me/agent/key", status_code=204)
    def delete_key(session: Annotated[Session, Depends(current)]) -> Response:
        hub.keys.drop_provider(session.sid)  # every key this session put
        return Response(status_code=204)

    return router


async def _siwc_status_multi(hub: Hub, person: str) -> dict[str, Any]:
    """This person's own ChatGPT account. Nobody else's account is shown, and there is no owner
    binding to fail: the bridge keeps one account per person."""
    account = siwc_accounts(hub).for_person(person)
    if account is None:
        return {"available": False, "signedIn": False}
    try:
        status = await account.call("GET", "/status")
    except NotConfigured as exc:
        return {"available": False, "signedIn": False, "reason": exc.user_message}
    signed_in = bool(status.get("signedIn"))
    error = status.get("error")
    out: dict[str, Any] = {
        "available": True,
        "signedIn": signed_in,
        "signingIn": bool(status.get("signingIn")),
        # Masked: the page asks the person to confirm it is the account they meant.
        "email": mask_email((status.get("account") or {}).get("email")) if signed_in else None,
        "error": error,
        "multi": True,
    }
    if isinstance(error, dict) and isinstance(error.get("message"), str):  # pyright: ignore[reportUnknownMemberType]
        out["reason"] = error["message"]
    if leave_offer_pending(hub.store, person) and signed_in:
        out["offerDisconnect"] = True
    return out


async def _siwc_login_multi(hub: Hub, person: str, *, switch: bool) -> dict[str, Any]:
    account = siwc_accounts(hub).for_person(person)
    client = models_mod.SIWC
    if account is None or client is None:
        raise Conflict("Sign in with ChatGPT is not set up on this server")
    caps = await client.bridge_capabilities()
    origins = _auth_origins(caps)
    sign_in: Any = caps.get("signIn")
    paste = isinstance(sign_in, dict) and sign_in.get("pasteCallback") is True  # pyright: ignore[reportUnknownMemberType]
    try:
        out = await account.call("POST", "/login", {"newProfile": True} if switch else None)
    except NotConfigured as exc:
        raise _bridge_problem(exc) from None
    return {
        "url": check_sign_in_url(out.get("url"), origins),
        "authOrigins": origins,
        "pasteCallback": paste,
    }


def _apply_to_seats(hub: Hub, pid: str, prefs: AgentPrefs) -> None:
    """What a person chose under Your agent reaches the agents they already have in rooms:
    a lower autonomy at once, and provider and model for a Consilith agent. A room's drawer
    can still change them for that room afterwards."""
    for room_id, _role in hub.store.seats_of(pid):
        room = hub.room(room_id)
        aid = f"{pid}.agent"
        cfg = room.agent_config(aid)
        update: dict[str, Any] = {}
        # Lowering reaches every seat at once; raising applies to new seats only (decision 1).
        lowered = autonomy_for_existing_seat(cfg.autonomy, prefs.autonomy)
        if lowered is not None:
            update["autonomy"] = lowered
        if prefs.kind == "consilith" and (
            cfg.model_provider != prefs.provider or cfg.model_name != prefs.model
        ):
            update["model_provider"] = prefs.provider
            update["model_name"] = prefs.model
        if update:
            room.set_agent_config(pid, aid, cfg.model_copy(update=update))
