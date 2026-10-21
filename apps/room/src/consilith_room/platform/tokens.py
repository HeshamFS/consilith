"""HMAC bearer tokens. Verification fails closed: any doubt is a refusal.

Kinds:
- ``person``: a person, for the HTTP API and AG-UI; it carries the id of one session (``sid``),
  which sign-out revokes, so a person token without a live session is refused;
- ``agent``: a hosted agent calling the room's A2A endpoints, bound to one room.
Brought (remote) agents never receive a token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Literal

Kind = Literal["person", "agent"]


class TokenError(Exception):
    """The token is missing, malformed, forged, expired or of the wrong kind."""


@dataclass(frozen=True)
class Claims:
    sub: str
    kind: Kind
    exp: float
    room: str | None = None
    sid: str | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class TokenIssuer:
    def __init__(self, secret: str, ttl_s: int) -> None:
        if len(secret) < 16:
            raise ValueError("token secret too short")
        self._key = secret.encode("utf-8")
        self._ttl = ttl_s

    def issue(
        self, sub: str, kind: Kind = "person", room: str | None = None, sid: str | None = None
    ) -> str:
        if kind == "agent" and not room:
            raise ValueError("an agent token is bound to one room")
        if kind == "person" and not sid:
            raise ValueError("a person token belongs to one session")
        claims = {
            "sub": sub,
            "kind": kind,
            "exp": time.time() + self._ttl,
            "room": room,
            "sid": sid,
        }
        body = _b64(json.dumps(claims).encode())
        sig = _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def verify(self, token: str, kind: Kind = "person") -> Claims:
        try:
            body, sig = token.split(".", 1)
            want = _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(want, sig):
                raise TokenError("bad signature")
            data = json.loads(_unb64(body))
            claims = Claims(
                sub=str(data["sub"]),
                kind=data["kind"],
                exp=float(data["exp"]),
                room=data.get("room"),
                sid=data.get("sid"),
            )
        except TokenError:
            raise
        except Exception as exc:
            raise TokenError("malformed token") from exc
        if claims.kind != kind:
            raise TokenError(f"expected a {kind} token")
        if claims.exp < time.time():
            raise TokenError("expired")
        if kind == "person" and not claims.sid:
            raise TokenError("no session")
        return claims
