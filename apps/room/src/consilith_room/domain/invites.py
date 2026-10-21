"""Invites: the only way into a room besides creating it (M9 "Admission").

- An invite belongs to one room and is stored as a SHA-256 digest of its secret.
- An email invite is for one address and spends once; the room link spends never but expires
  in seven days and can be revoked.
- A room has four seats at most (enforced where the seat is given, ``RoomCore.join``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from consilith_contracts import new_id

from ..platform.links import digest_of, new_secret
from ..platform.store import Store
from .errors import InviteError


@dataclass(frozen=True)
class Created:
    id: str
    token: str
    expires: float


class Invites:
    def __init__(self, store: Store, ttl_s: float) -> None:
        self.store = store
        self.ttl_s = ttl_s

    def create(self, room: str, created_by: str, email: str | None) -> Created:
        token = new_secret()
        invite_id = new_id("inv")
        expires = time.time() + self.ttl_s
        self.store.add_invite(
            invite_id,
            room,
            "email" if email else "link",
            email,
            digest_of(token),
            created_by,
            expires,
        )
        return Created(invite_id, token, expires)

    def lookup(self, token: str) -> dict[str, Any]:
        """The invite this secret opens, if it can still be used."""
        found = self.store.invite_by_digest(digest_of(token))
        if found is None:
            raise InviteError("unknown")
        if found["revoked_at"] is not None:
            raise InviteError("revoked")
        if found["used_at"] is not None:
            raise InviteError("used")
        if found["expires"] <= time.time():
            raise InviteError("expired")
        return found

    def revoke(self, invite_id: str) -> None:
        self.store.revoke_invite(invite_id)
