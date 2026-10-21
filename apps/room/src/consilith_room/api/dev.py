"""The dev mailbox: emailed links, readable on loopback only, never in production.

``GET /dev/mailbox`` answers only when all of this holds:
- ``CONSILITH_DEV_MAILBOX=1`` and the environment is not production (otherwise the route does
  not exist);
- the ``X-Consilith-Dev`` header carries this start's capability secret;
- the ``Host`` (and ``Origin``, when sent) name a loopback host;
- no forwarding header is present: a request that passed through a proxy is not a loopback
  request, and a forged ``X-Forwarded-Host`` never makes one.
The capability is new at every start and written to ``var/dev.env``; the Vite dev proxy adds
the header for ``/api/dev/*`` only, so the browser never holds it.
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, Request

from ..domain.errors import Denied
from ..platform.mail import DevMailbox
from .hub import Hub

CAPABILITY_KEY = "CONSILITH_DEV_CAPABILITY"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
FORWARDING = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip")


def _host(value: str) -> str:
    """The host of a Host header value, without port or brackets."""
    return (urlsplit(f"//{value}").hostname or "").lower()


def write_capability(path: Path, capability: str) -> None:
    """Put this start's capability in ``var/dev.env``, keeping every other line."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [ln for ln in lines if not ln.startswith(f"{CAPABILITY_KEY}=")]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text("\n".join([*kept, f"{CAPABILITY_KEY}={capability}"]) + "\n", encoding="utf-8")
    tmp.replace(path)


def dev_router(hub: Hub) -> APIRouter:
    router = APIRouter()
    mailbox = hub.mailer
    capability = hub.dev_capability
    assert isinstance(mailbox, DevMailbox) and capability is not None

    @router.get("/dev/mailbox")
    def read_mailbox(
        request: Request,
        x_consilith_dev: Annotated[str | None, Header()] = None,
    ) -> list[dict[str, Any]]:
        if not x_consilith_dev or not hmac.compare_digest(x_consilith_dev, capability):
            raise Denied("the dev mailbox needs this start's capability")
        if _host(request.headers.get("host", "")) not in LOOPBACK:
            raise Denied("the dev mailbox answers on a loopback host only")
        origin = request.headers.get("origin")
        if origin is not None and _host(origin.split("://", 1)[-1]) not in LOOPBACK:
            raise Denied("the dev mailbox answers on a loopback origin only")
        if any(h in request.headers for h in FORWARDING):
            raise Denied("the dev mailbox does not answer forwarded requests")
        return [dict(m) for m in mailbox.messages()]

    return router
