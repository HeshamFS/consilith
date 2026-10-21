"""GitHub sign-in: the authorize URL (PKCE S256) and the server-side exchange.

The exchange is a port, so tests fake GitHub. ``HttpGitHub`` is the real one: it trades the code
and the PKCE verifier for a token, reads the user and their emails, and keeps only the stable
GitHub user id, the login, the name and the *verified primary* email. The access token lives
for these three calls and is never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API = "https://api.github.com"
TIMEOUT_S = 10.0


class GitHubError(RuntimeError):
    """GitHub refused, or answered with something unusable. Carries no upstream text."""


@dataclass(frozen=True)
class GitHubProfile:
    id: str
    login: str
    name: str
    email: str | None  # the verified primary email, if the user has one


class GitHubClient(Protocol):
    async def profile(self, code: str, verifier: str) -> GitHubProfile: ...


def pkce_challenge(verifier: str) -> str:
    """S256: base64url(sha256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, callback_url: str, state: str, verifier: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback_url,  # fixed by settings, never from the request
            "scope": "read:user user:email",
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "allow_signup": "true",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class HttpGitHub:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        callback_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._id = client_id
        self._secret = client_secret
        self._callback = callback_url
        self._transport = transport

    def __repr__(self) -> str:
        return f"HttpGitHub(client_id={self._id!r}, secret=[redacted])"

    async def profile(self, code: str, verifier: str) -> GitHubProfile:
        async with httpx.AsyncClient(
            transport=self._transport, timeout=TIMEOUT_S, follow_redirects=False
        ) as http:
            token = await self._token(http, code, verifier)
            auth = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            user = await self._get(http, "/user", auth)
            emails = await self._get(http, "/user/emails", auth)
        uid = user.get("id")
        login = user.get("login")
        if not isinstance(uid, int) or not isinstance(login, str) or not login:
            raise GitHubError("GitHub returned no user")
        name = user.get("name")
        return GitHubProfile(
            id=str(uid),
            login=login,
            name=name if isinstance(name, str) else "",
            email=_verified_primary(emails),
        )

    async def _token(self, http: httpx.AsyncClient, code: str, verifier: str) -> str:
        try:
            resp = await http.post(
                TOKEN_URL,
                headers={"Accept": "application/json"},
                json={
                    "client_id": self._id,
                    "client_secret": self._secret,
                    "code": code,
                    "redirect_uri": self._callback,
                    "code_verifier": verifier,
                },
            )
            data = cast(dict[str, Any], resp.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubError("the GitHub token exchange failed") from exc
        token = data.get("access_token")
        if resp.status_code != 200 or not isinstance(token, str) or not token:
            raise GitHubError("GitHub refused the code")
        return token

    async def _get(self, http: httpx.AsyncClient, path: str, headers: dict[str, str]) -> Any:
        try:
            resp = await http.get(f"{API}{path}", headers=headers)
            if resp.status_code != 200:
                raise GitHubError(f"GitHub answered {resp.status_code} for {path}")
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubError(f"the GitHub request for {path} failed") from exc


def _verified_primary(emails: Any) -> str | None:
    if not isinstance(emails, list):
        return None
    for item in cast(list[Any], emails):
        if isinstance(item, dict):
            row = cast(dict[str, Any], item)
            addr = row.get("email")
            if row.get("primary") is True and row.get("verified") is True and isinstance(addr, str):
                return addr.strip().lower()
    return None
