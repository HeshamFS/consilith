"""Accounts: real people, created at first sign-in (M9). Nobody is seeded.

An id is fixed once, from the email's local part. The profile (name, colour, home discipline)
is the person's own. The room document carries only a seat's id, name and colour, never an email.
"""

from __future__ import annotations

import re
from typing import Any

from ..platform.store import Store
from .errors import NotFound, RoomError
from .roles import COLORS

EMAIL = re.compile(r"^[^@\s,;<>()\[\]\\\"]+@[^@\s,;<>()\[\]\\\"]+\.[^@\s,;<>()\[\]\\\"]+$")
# Ids no person may take: they name room agents, the room server or the backend.
RESERVED = frozenset(
    {"conductor", "swarm", "solvers", "referee", "room", "backend", "system", "admin", "root"}
)


def normalize_email(raw: str) -> str | None:
    """The address in canonical form, or None when it is not an email address."""
    email = raw.strip().lower()
    if len(email) > 254 or not EMAIL.match(email):
        return None
    return email


def base_id(source: str) -> str:
    """``researcher.alpha@example.test`` -> ``researcher``: before the first ``.``, ``+`` or ``_``
    of the local part, lowercased, letters and digits only. A GitHub login is used whole."""
    local = source.split("@", 1)[0]
    head = re.split(r"[._+]", local, maxsplit=1)[0] if "@" in source else source
    cleaned = re.sub(r"[^a-z0-9]", "", head.lower())
    return cleaned or "user"


def public(account: dict[str, Any]) -> dict[str, Any]:
    """The ``person`` object of the web API."""
    return {
        "id": account["id"],
        "email": account["email"],
        "name": account["name"],
        "color": account["color"],
        "discipline": account["discipline"],
        "profileComplete": account["profile_complete"],
    }


class Accounts:
    def __init__(self, store: Store) -> None:
        self.store = store

    def for_email(self, email: str) -> dict[str, Any]:
        """The account of this email, created at first sign-in."""
        found = self.store.person_by_email(email)
        if found is not None:
            return found
        return self.store.create_account(
            base_id(email), email=email, color=self._first_color(), reserved=RESERVED
        )

    def for_github(
        self, github_id: str, login: str, name: str, email: str | None
    ) -> dict[str, Any]:
        """The account of a GitHub user: the one that has this GitHub id; else the account of
        the user's *verified primary* email (and that account is linked); else a new one."""
        found = self.store.person_by_github(github_id)
        if found is not None:
            return found
        if email is not None:
            existing = self.store.person_by_email(email)
            if existing is not None:
                if existing["github_id"] is not None:
                    raise RoomError(
                        "that email belongs to an account linked to another GitHub user"
                    )
                self.store.link_github(existing["id"], github_id)
                return self.require(existing["id"])
        return self.store.create_account(
            base_id(email if email is not None else login),
            email=email,
            github_id=github_id,
            name=name[:60],
            color=self._first_color(),
            reserved=RESERVED,
        )

    def _first_color(self) -> str:
        names = list(COLORS)
        return names[self.store.account_count() % len(names)]

    def require(self, pid: str) -> dict[str, Any]:
        found = self.store.person(pid)
        if found is None:
            raise NotFound(f"no person {pid}")
        return found

    def update_profile(self, pid: str, name: str, color: str, discipline: str) -> dict[str, Any]:
        name = " ".join(name.split())
        if not name or len(name) > 60 or not name.isprintable():
            raise RoomError("a name is 1 to 60 printable characters")
        if color not in COLORS:
            raise RoomError(f"colour is one of {', '.join(COLORS)}")
        if len(discipline) > 100 or (discipline and not discipline.isprintable()):
            raise RoomError("discipline is at most 100 printable characters")
        self.require(pid)
        self.store.set_profile(pid, name, color, discipline)
        return self.require(pid)
