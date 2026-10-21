"""Your agent: the choice made once (provider, model, autonomy, brought card) and the session key.

A key lives in server memory for the signed-in session that put it. It is never returned, stored,
journaled or logged (platform/keys.py).
"""

import asyncio
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response

from ..agents import models as models_mod
from ..agents.models import NotConfigured, list_models
from ..domain.errors import Conflict, Denied, RoomError
from ..domain.prefs import DEFAULT_MODELS, AgentPrefs, default_prefs
from ..protocols.outbound import OutboundPolicy, OutboundRefused
from .auth import Session
from .hub import Hub
from .schemas import KeyIn


def prefs_of(hub: Hub, pid: str) -> AgentPrefs:
    stored = hub.store.agent_prefs(pid)
    if stored is not None:
        return AgentPrefs.model_validate(stored)
    return default_prefs(str(hub.accounts.require(pid)["discipline"]))


def agent_view(hub: Hub, session: Session) -> dict[str, Any]:
    prefs = prefs_of(hub, session.person)
    model = prefs.model or DEFAULT_MODELS.get(prefs.provider)
    return {
        "kind": prefs.kind,
        "provider": prefs.provider,
        "model": model if prefs.kind == "consilith" else None,
        "autonomy": prefs.autonomy.value,
        "cardUrl": prefs.card_url,
        "hasKey": hub.keys.has(session.person, session.sid, prefs.provider),
    }


async def check_card_url(policy: OutboundPolicy, card_url: str) -> None:
    """A card URL passes the brought-agent outbound policy before it is stored or used."""
    try:
        policy.check_url(card_url, card=True)
        parts = urlsplit(card_url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        await asyncio.to_thread(policy.resolve, parts.hostname or "", port)
    except OutboundRefused as exc:
        raise Denied(f"agent card refused: {exc}") from exc


def me_agent_router(hub: Hub, current: Callable[..., Session]) -> APIRouter:
    router = APIRouter()

    @router.get("/me/agent")
    def get_agent(session: Annotated[Session, Depends(current)]) -> dict[str, Any]:
        return agent_view(hub, session)

    @router.put("/me/agent")
    async def put_agent(
        body: AgentPrefs, session: Annotated[Session, Depends(current)]
    ) -> dict[str, Any]:
        problem = body.problems()
        if problem is not None:
            raise RoomError(problem)
        if body.card_url is not None:
            await check_card_url(hub.runtime.brought.policy, body.card_url)
        hub.store.put_agent_prefs(session.person, body.wire())
        _apply_to_seats(hub, session.person, body)
        return agent_view(hub, session)

    @router.put("/me/agent/key", status_code=204)
    def put_key(body: KeyIn, session: Annotated[Session, Depends(current)]) -> Response:
        hub.keys.put(session.person, session.sid, body.provider, body.key.get_secret_value())
        return Response(status_code=204)

    @router.get("/me/agent/models")
    async def get_models(
        provider: str, session: Annotated[Session, Depends(current)]
    ) -> dict[str, Any]:
        """The models the key this session holds for ``provider`` can use, newest first."""
        if provider not in ("anthropic", "openai", "gemini", "siwc"):
            raise RoomError("models are listed for anthropic, openai, gemini and siwc")
        if provider == "siwc" and (
            models_mod.SIWC is None or models_mod.SIWC.owner != session.person
        ):
            raise Conflict("connect your ChatGPT plan first")
        key = (
            ""
            if provider == "siwc"
            else hub.keys.for_session(session.person, session.sid, provider)
        )
        if key is None:
            raise Conflict("add your API key for this provider first")
        try:
            models = await list_models(provider, key)
        except NotConfigured as exc:
            raise RoomError(str(exc)) from None
        return {"provider": provider, "models": models}

    # Sign in with ChatGPT, through the local sidecar (docs/siwc.md). The ChatGPT session lives
    # on this machine in the sidecar; the room never sees its tokens.
    @router.get("/me/agent/siwc")
    async def siwc_status(session: Annotated[Session, Depends(current)]) -> dict[str, Any]:
        client = models_mod.SIWC
        if client is None or not client.available:
            return {"available": False, "signedIn": False}
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
        return {
            "available": True,
            "signedIn": signed_in,
            "signingIn": bool(status.get("signingIn")),
            "email": (status.get("account") or {}).get("email") if signed_in else None,
            "error": status.get("error"),
        }

    @router.post("/me/agent/siwc/login")
    async def siwc_login(session: Annotated[Session, Depends(current)]) -> dict[str, Any]:
        client = models_mod.SIWC
        if client is None or not client.available:
            raise Conflict("Sign in with ChatGPT is not set up on this server")
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
        if client.owner != session.person:
            raise Denied("only the person who connected this ChatGPT plan can disconnect it")
        try:
            await client.call("POST", "/logout")
        except NotConfigured as exc:
            raise RoomError(str(exc)) from None
        client.set_owner(None)
        return {"signedIn": False}

    @router.delete("/me/agent/key", status_code=204)
    def delete_key(session: Annotated[Session, Depends(current)]) -> Response:
        hub.keys.drop_provider(session.sid)  # every key this session put
        return Response(status_code=204)

    return router


def _apply_to_seats(hub: Hub, pid: str, prefs: AgentPrefs) -> None:
    """What a person chose under Your agent reaches the agents they already have in rooms:
    autonomy always, and provider and model for a Consilith agent. A room's drawer can still
    change them for that room afterwards."""
    for room_id, _role in hub.store.seats_of(pid):
        room = hub.room(room_id)
        aid = f"{pid}.agent"
        cfg = room.agent_config(aid)
        update: dict[str, Any] = {}
        if cfg.autonomy != prefs.autonomy:
            update["autonomy"] = prefs.autonomy
        if prefs.kind == "consilith" and (
            cfg.model_provider != prefs.provider or cfg.model_name != prefs.model
        ):
            update["model_provider"] = prefs.provider
            update["model_name"] = prefs.model
        if update:
            room.set_agent_config(pid, aid, cfg.model_copy(update=update))
