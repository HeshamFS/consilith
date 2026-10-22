"""Plugin apps: a stored HTML artifact served to a seated member, in a locked-down frame.

A plugin can return a self-contained page (an interactive viewer, a report). The room serves
it from ``GET /rooms/{id}/apps/{sha256}`` only to a seated member, only when the stored record
is ``text/html`` with the role ``app`` or ``report``, and always under a Content-Security-Policy
whose ``sandbox`` directive keeps the document at an opaque origin even if the URL is opened
directly. The policy is a fixed constant: nothing in it comes from the artifact.

A frame cannot send an ``Authorization`` header, so the page asks ``POST .../link`` (with its
bearer token) for a short-lived address bound to one room, one artifact and one person. The
address is checked again on every fetch: the session must still be live and the person still
seated.
"""

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response

from ..domain.artifacts import owns_artifact
from ..domain.errors import Denied, NotFound, RoomError
from ..domain.service import RoomService
from .auth import Session
from .hub import Hub

APP_ROLES = ("app", "report")
APP_MEDIA_TYPE = "text/html"
LINK_TTL_S = 300
MAX_LINKS = 512
SHA = re.compile(r"^[a-f0-9]{64}$")

# Extra origins a report may load its front end from, by directive. Fixed here, never taken
# from the artifact. The marimo HTML export loads its front end from this one CDN (WS-MX).
REPORT_ORIGINS: dict[str, tuple[str, ...]] = {
    "script-src": ("https://cdn.jsdelivr.net",),
    "style-src": ("https://cdn.jsdelivr.net",),
    "font-src": ("https://cdn.jsdelivr.net",),
    "img-src": ("https://cdn.jsdelivr.net",),
}


class NotAnApp(RoomError):
    """The artifact exists but is not a page the room will serve as an app."""

    status = 415
    code = "not_an_app"


def content_security_policy(role: str) -> str:
    """The policy for one app role. ``app`` gets nothing beyond inline code and data images."""
    extra = REPORT_ORIGINS if role == "report" else {}

    def source(directive: str, base: str) -> str:
        return " ".join([directive, base, *extra.get(directive, ())])

    return "; ".join(
        [
            "sandbox allow-scripts",
            "default-src 'none'",
            source("script-src", "'unsafe-inline'"),
            source("style-src", "'unsafe-inline'"),
            source("img-src", "data: blob:"),
            source("font-src", "data:"),
            "connect-src 'none'",
            "frame-ancestors 'self'",
        ]
    )


def app_headers(role: str) -> dict[str, str]:
    return {
        "Content-Security-Policy": content_security_policy(role),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, max-age=300",
    }


@dataclass(frozen=True)
class _Link:
    room: str
    sha256: str
    person: str
    sid: str
    expires: float


def apps_router(
    hub: Hub,
    current: Callable[..., Session],
    seated: Callable[[str, str], RoomService],
) -> APIRouter:
    router = APIRouter()
    links: dict[str, _Link] = {}

    def prune(now: float) -> None:
        for key in [k for k, v in links.items() if v.expires < now]:
            del links[key]
        while len(links) >= MAX_LINKS:  # oldest first: dicts keep insertion order
            del links[next(iter(links))]

    def stored_app(room: RoomService, room_id: str, sha256: str) -> tuple[str, bytes]:
        """The role and bytes of an app artifact this room holds, or the refusal."""
        if not owns_artifact(room, hub.store, sha256):
            raise NotFound("artifact not found in this room")
        stored = hub.store.artifact(sha256)
        if stored is None:
            raise NotFound("artifact not found in this room")
        media, content = stored
        roles: set[str] = set()
        results: dict[str, dict[str, Any]] = room.read("results") or {}
        for record in results.values():
            if record.get("room") != room_id:
                continue
            for ref in record.get("artifacts", []):
                if ref.get("sha256") == sha256:
                    roles.add(str(ref.get("role", "")))
        role = next((r for r in APP_ROLES if r in roles), None)
        if media.split(";", 1)[0].strip().lower() != APP_MEDIA_TYPE or role is None:
            raise NotAnApp("this artifact is not an app or a report")
        return role, content

    @router.post("/rooms/{room_id}/apps/{sha256}/link")
    def link(
        room_id: str,
        sha256: str,
        session: Annotated[Session, Depends(current)],
    ) -> dict[str, Any]:
        if not SHA.match(sha256):
            raise NotFound("artifact not found in this room")
        room = seated(room_id, session.person)
        stored_app(room, room_id, sha256)
        now = time.time()
        prune(now)
        token = secrets.token_urlsafe(24)
        links[token] = _Link(room_id, sha256, session.person, session.sid, now + LINK_TTL_S)
        return {
            "url": f"/rooms/{room_id}/apps/{sha256}?t={token}",
            "expiresIn": LINK_TTL_S,
        }

    @router.get("/rooms/{room_id}/apps/{sha256}")
    def app(
        room_id: str,
        sha256: str,
        t: Annotated[str | None, Query(max_length=64)] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        if t is not None:
            entry = links.get(t)
            if (
                entry is None
                or entry.expires < time.time()
                or (entry.room, entry.sha256) != (room_id, sha256)
                or not hub.store.session_live(entry.sid, entry.person)
            ):
                raise Denied("this app link is not valid; open it again")
            person = entry.person
        else:
            person = current(authorization).person
        room = seated(room_id, person)
        role, content = stored_app(room, room_id, sha256)
        return Response(content, media_type="text/html; charset=utf-8", headers=app_headers(role))

    return router
