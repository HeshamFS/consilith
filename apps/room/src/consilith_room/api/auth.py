"""Sign-in, sessions and the profile (M9 "Accounts and sessions").

- A sign-in link: 32 random bytes kept as a SHA-256 digest, purpose ``signin``, bound to one
  email, 15 minutes, redeemed once in one statement.
- GitHub: ``state`` (32 random bytes, single use, 10 minutes) is bound to the starting browser
  by an HttpOnly SameSite=Lax cookie; PKCE S256; the callback URL comes from settings; return
  paths are relative only; accounts map to the GitHub user id.
- Hosted mode (``CONSILITH_ENV=hosted``): state-changing calls must come from an allowed origin,
  sign-in calls are rate limited per client, cookies are Secure, and the room owner (or an
  operator) makes a single-use, expiring invite link for a named email. The link is kept as a
  SHA-256 digest, purpose ``invite_signin``, and signs in that email only.
- Every sign-in ends in a session. The person token carries the session id; sign-out revokes it.
"""

import hmac
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field

from ..domain.accounts import normalize_email, public
from ..domain.errors import Denied, InvalidLink, NotFound, RoomError
from ..platform.links import digest_of, new_secret
from ..platform.mail import MailError
from ..platform.settings import Settings
from ..platform.tokens import TokenError
from ..protocols.github import GitHubError, authorize_url
from .hub import Hub
from .schemas import Body, EmailIn, ProfileIn, TicketIn, TokenIn

logger = logging.getLogger("consilith.auth")
OAUTH_COOKIE = "consilith_oauth"
STATE_TTL_S = 600
TICKET_TTL_S = 60
INVITE_PURPOSE = "invite_signin"


class InviteLinkIn(Body):
    email: str = Field(max_length=320)


class InviteRedeemIn(Body):
    token: str = Field(min_length=1, max_length=512)
    email: str = Field(max_length=320)


class RateLimiter:
    """Sliding-window limiter kept in memory: at most ``limit`` hits per ``window_s`` per key.
    Memory is bounded: a key with no recent hit is dropped, and the key count is capped."""

    MAX_KEYS = 4096

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Count one attempt. None when allowed, else the seconds until it would be."""
        now = time.monotonic()
        if len(self._hits) > self.MAX_KEYS:
            self._hits = {k: q for k, q in self._hits.items() if q and q[-1] > now - self.window_s}
        queue = self._hits.setdefault(key, deque())
        while queue and queue[0] <= now - self.window_s:
            queue.popleft()
        if len(queue) >= self.limit:
            return max(1.0, queue[0] + self.window_s - now)
        queue.append(now)
        return None


class TooManyAttempts(RoomError):
    status = 429
    code = "rate_limited"

    def __init__(self, retry_after_s: float) -> None:
        super().__init__(
            "too many sign-in attempts; wait a minute and try again",
            retry_after_s=round(retry_after_s),
        )


@dataclass(frozen=True)
class Session:
    person: str
    sid: str


def safe_return(path: str | None) -> str:
    """A relative path on this app, or a refusal. Never a URL, never protocol-relative."""
    if path is None or path == "":
        return "/"
    parts = urlsplit(path)
    bad = (
        not path.startswith("/")
        or path.startswith(("//", "/\\"))
        or "\\" in path
        or parts.scheme
        or parts.netloc
        or len(path) > 512
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
    )
    if bad:
        raise RoomError("return must be a relative path such as /rooms", code="bad_return")
    return path


def make_auth(hub: Hub) -> tuple[Callable[..., Session], Callable[..., str]]:
    """The two dependencies every protected route uses. They fail closed: no token, a bad one,
    an unknown person, or a session that ended or was signed out is a refusal."""

    def current(authorization: Annotated[str | None, Header()] = None) -> Session:
        if not authorization or not authorization.startswith("Bearer "):
            raise Denied("a bearer token is required")
        try:
            claims = hub.tokens.verify(authorization.removeprefix("Bearer ").strip(), kind="person")
        except TokenError as exc:
            raise Denied(f"invalid token: {exc}") from exc
        if claims.sid is None or not hub.store.session_live(claims.sid, claims.sub):
            raise Denied("this session has ended; sign in again")
        if hub.store.person(claims.sub) is None:
            raise Denied("unknown person")
        return Session(claims.sub, claims.sid)

    def person(session: Annotated[Session, Depends(current)]) -> str:
        return session.person

    return current, person


def start_session(hub: Hub, settings: Settings, pid: str) -> dict[str, Any]:
    sid = secrets.token_urlsafe(12)
    hub.store.create_session(sid, pid, settings.token_ttl_s)
    account = hub.accounts.require(pid)
    return {
        "token": hub.tokens.issue(pid, "person", sid=sid),
        "sid": sid,
        "person": public(account),
        "needsProfile": not account["profile_complete"],
    }


def client_key(request: Request, settings: Settings) -> str:
    """Who is calling, for rate limits. Hosted, the only way in is the tunnel, which sets
    ``CF-Connecting-IP``; anything else falls back to the socket peer."""
    if settings.is_hosted:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            return forwarded[:64]
    return request.client.host if request.client else "unknown"


def auth_router(hub: Hub, settings: Settings, current: Callable[..., Session]) -> APIRouter:
    store = hub.store
    hosted = settings.is_hosted
    per_client = RateLimiter(settings.signin_rate_per_min)
    per_email = RateLimiter(max(3, settings.signin_rate_per_min // 2))
    overall = RateLimiter(settings.signin_rate_per_min * 6)  # a forged client header cannot dodge

    def same_origin(request: Request) -> None:
        """Hosted: a call that changes anything must name an allowed origin."""
        if not hosted or request.method in ("GET", "HEAD", "OPTIONS"):
            return
        origin = request.headers.get("origin", "").rstrip("/").lower()
        if origin not in settings.origins:
            raise Denied("this origin may not call the sign-in API", code="origin_not_allowed")

    def limited(request: Request, email: str | None = None) -> None:
        """Hosted sign-in rate limits: per client, per email, and overall."""
        if not hosted:
            return
        checks = [(overall, "all"), (per_client, client_key(request, settings))]
        if email is not None:
            checks.append((per_email, email))
        for limiter, key in checks:
            wait = limiter.hit(key)
            if wait is not None:
                raise TooManyAttempts(wait)

    router = APIRouter(dependencies=[Depends(same_origin)])

    @router.get("/auth/providers")
    def providers() -> dict[str, bool]:
        return {
            "email": True,
            "github": hub.github is not None,
            "devMailbox": settings.dev_mailbox and settings.env != "production" and not hosted,
            "hosted": hosted,
        }

    @router.post("/auth/email", status_code=202)
    def email_link(body: EmailIn, request: Request) -> dict[str, bool]:
        """Always the same answer, whatever the address: nobody learns who has an account."""
        email = normalize_email(body.email)
        limited(request, email)
        if email is not None:
            token = new_secret()
            store.put_one_time(
                digest_of(token), "signin", email, {}, time.time() + settings.signin_ttl_s
            )
            url = f"{settings.web_origin.rstrip('/')}/auth/verify?token={token}"
            try:
                hub.mailer.send(email, "Your Consilith sign-in link", url)
            except MailError as exc:
                logger.error("sign-in mail not sent: %s", exc)
        return {"sent": True}

    @router.post("/auth/email/redeem")
    def redeem_email(body: TokenIn, request: Request) -> dict[str, Any]:
        limited(request)
        found = store.consume_one_time(digest_of(body.token), "signin")
        if found is None:
            raise InvalidLink()
        account = hub.accounts.for_email(found[0])
        return start_session(hub, settings, account["id"])

    @router.post("/auth/ticket/redeem")
    def redeem_ticket(body: TicketIn, request: Request) -> dict[str, Any]:
        limited(request)
        found = store.consume_one_time(digest_of(body.ticket), "ticket")
        if found is None:
            raise InvalidLink("invalid_ticket")
        return start_session(hub, settings, found[0])

    @router.post("/auth/logout", status_code=204)
    def logout(session: Annotated[Session, Depends(current)]) -> Response:
        store.revoke_session(session.sid)
        hub.keys.drop_session(session.sid)
        return Response(status_code=204)

    # invite-link sign-in (hosted) ------------------------------------------------------------
    def may_invite(pid: str) -> bool:
        """The room owner of any of their rooms, or a listed operator."""
        if pid in settings.operators:
            return True
        return any(store.room_owner(room) == pid for room, _role in store.seats_of(pid))

    def require_hosted() -> None:
        if not hosted:
            raise NotFound("invite-link sign-in is for hosted mode", code="not_configured")

    @router.post("/auth/invite-links", status_code=201)
    def make_invite_link(
        body: InviteLinkIn, request: Request, session: Annotated[Session, Depends(current)]
    ) -> dict[str, Any]:
        """A single-use link that signs in the named email, and nobody else. Never mailed: the
        owner passes it on. Only its digest is kept."""
        require_hosted()
        limited(request, f"invite:{session.person}")
        if not may_invite(session.person):
            raise Denied("only a room owner makes sign-in links", code="not_room_owner")
        email = normalize_email(body.email)
        if email is None:
            raise RoomError("that is not an email address", code="bad_email")
        token = new_secret()
        expires = time.time() + settings.hosted_invite_ttl_s
        store.put_one_time(digest_of(token), INVITE_PURPOSE, email, {"by": session.person}, expires)
        url = f"{settings.web_origin.rstrip('/')}/api/auth/invite-links/follow?token={token}"
        return {"url": url, "email": email, "expiresAt": expires}

    def redeem_invite(token: str, email: str | None) -> str:
        """The account id the link signs in. Spent either way: a wrong email burns the link."""
        found = store.consume_one_time(digest_of(token), INVITE_PURPOSE)
        if found is None:
            raise InvalidLink()
        if email is not None and normalize_email(email) != found[0]:
            raise InvalidLink()
        return str(hub.accounts.for_email(found[0])["id"])

    @router.post("/auth/invite-links/redeem")
    def redeem_invite_link(body: InviteRedeemIn, request: Request) -> dict[str, Any]:
        """The invited person types the email the link was made for, then gets a session."""
        require_hosted()
        limited(request, normalize_email(body.email))
        return start_session(hub, settings, redeem_invite(body.token, body.email))

    @router.get("/auth/invite-links/follow")
    def follow_invite_link(
        request: Request, token: Annotated[str, Query(max_length=512)]
    ) -> Response:
        """Opening the link in a browser: a one-minute ticket, then the web app's finish page."""
        require_hosted()
        limited(request)
        pid = redeem_invite(token, None)
        ticket = new_secret()
        store.put_one_time(digest_of(ticket), "ticket", pid, {}, time.time() + TICKET_TTL_S)
        resp = RedirectResponse(
            f"{settings.web_origin.rstrip('/')}/auth/finish?{urlencode({'ticket': ticket})}",
            status_code=302,
        )
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    # GitHub ---------------------------------------------------------------------------------
    @router.get("/auth/github/start")
    def github_start(return_: Annotated[str | None, Query(alias="return")] = None) -> Response:
        if hub.github is None:
            raise NotFound("GitHub sign-in is not configured", code="not_configured")
        back = safe_return(return_)
        state, verifier, browser = new_secret(), new_secret(), new_secret()
        store.put_one_time(
            digest_of(state),
            "oauth_state",
            digest_of(browser),  # the browser this started in
            {"verifier": verifier, "return": back},
            time.time() + STATE_TTL_S,
        )
        url = authorize_url(
            settings.github_client_id, settings.github_callback_url, state, verifier
        )
        resp = RedirectResponse(url, status_code=302)
        resp.set_cookie(
            OAUTH_COOKIE,
            browser,
            max_age=STATE_TTL_S,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )
        return resp

    @router.get("/auth/github/callback")
    async def github_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> Response:
        if hub.github is None:
            raise NotFound("GitHub sign-in is not configured", code="not_configured")
        browser = request.cookies.get(OAUTH_COOKIE)
        if error or not code or not state or not browser:
            raise RoomError("the GitHub sign-in did not complete", code="invalid_state")
        found = store.consume_one_time(digest_of(state), "oauth_state")  # single use
        if found is None or not hmac.compare_digest(found[0], digest_of(browser)):
            raise RoomError("the GitHub sign-in did not complete", code="invalid_state")
        try:
            profile = await hub.github.profile(code, str(found[1]["verifier"]))
        except GitHubError as exc:
            logger.warning("GitHub sign-in failed: %s", exc)
            raise RoomError("GitHub could not sign you in", code="github_failed") from exc
        account = hub.accounts.for_github(profile.id, profile.login, profile.name, profile.email)
        ticket = new_secret()
        store.put_one_time(
            digest_of(ticket), "ticket", account["id"], {}, time.time() + TICKET_TTL_S
        )
        query = {"ticket": ticket}
        if found[1]["return"] != "/":
            query["return"] = str(found[1]["return"])
        resp = RedirectResponse(
            f"{settings.web_origin.rstrip('/')}/auth/finish?{urlencode(query)}", status_code=302
        )
        resp.delete_cookie(OAUTH_COOKIE, path="/")
        return resp

    # the profile ----------------------------------------------------------------------------
    @router.get("/me")
    def me(session: Annotated[Session, Depends(current)]) -> dict[str, Any]:
        return {"person": public(hub.accounts.require(session.person)), "sid": session.sid}

    @router.put("/me")
    def put_me(body: ProfileIn, session: Annotated[Session, Depends(current)]) -> JSONResponse:
        account = hub.accounts.update_profile(
            session.person, body.name, body.color, body.discipline
        )
        return JSONResponse({"person": public(account)})

    return router
