"""Admission (M9): a seat comes from creating a room or from accepting a valid invite.

Nothing else seats anyone. There is no open join. Four seats at most.
"""

import logging
import re
from collections.abc import Callable
from typing import Annotated, Any

from consilith_contracts.extensions import (
    all_extensions,
    default_case,
    plugin_for,
    plugins_for,
    registry_operation,
)
from fastapi import APIRouter, Depends, Response

from ..domain.accounts import normalize_email
from ..domain.errors import Conflict, Denied, NotFound, RoomError
from ..domain.prefs import AgentPrefs, seat_config
from ..domain.roles import COLORS, Role
from ..domain.service import RoomService
from ..platform.clock import iso
from ..platform.links import digest_of
from ..platform.mail import MailError
from ..platform.stores import run_leave_hooks, run_room_delete_hooks
from .hub import Hub
from .me import check_card_url, prefs_of
from .schemas import AcceptIn, AgentChoiceIn, NewInviteIn, NewRoomIn

logger = logging.getLogger("consilith.admission")
MAX_EMAIL_INVITES = 3  # four seats, one is the creator's


def _color_name(node: dict[str, Any]) -> str:
    named = node.get("colorName")
    if isinstance(named, str):
        return named
    return next((n for n, (paper, _s) in COLORS.items() if paper == node.get("color")), "blue")


def _seat(node: dict[str, Any]) -> dict[str, Any]:
    return {"id": node["id"], "name": node["name"], "color": _color_name(node)}


class Admission:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    # rooms ------------------------------------------------------------------------------------
    @registry_operation
    async def create_room(
        self,
        pid: str,
        room_id: str,
        title: str,
        studio: str,
        role: Role,
        emails: list[str],
        alone: bool = False,
    ) -> dict[str, Any]:
        hub = self.hub
        self._require_profile(pid)
        invitees = self._clean_emails(pid, emails)
        studio = studio or default_case()
        cases = all_extensions("cases")
        if studio not in cases:
            raise NotFound(f"no studio {studio}")
        owner = plugin_for("cases", studio)
        plugins = plugins_for([owner["id"]]) if owner else []
        roles = {item["id"] for plugin in plugins for item in plugin["roles"]}
        for plugin in plugins:
            for capability in plugin["capabilities"]:
                roles.update(lane.default_role for lane in cases[capability].lanes)
        if role not in roles:
            raise Denied("role is not supplied by the selected room plugin")
        room = hub.create_room(room_id, title, studio)
        prefs = self._prefs(pid)
        self._seat(room, pid, role, prefs)
        room.take_role_lanes(pid)
        if alone:
            room.take_every_lane(pid, studio)
        link = hub.invites.create(room_id, pid, None)
        url = self._url("invite", link.token)
        for email in invitees:
            self._invite_by_email(room, pid, email)
        out: dict[str, Any] = {"id": room.id, "seq": room.seq, "link": url}
        error = await self._bring(room, pid, prefs)
        if error:
            out["agentError"] = error
        out["seq"] = room.seq
        return out

    def list_rooms(self, pid: str) -> list[dict[str, Any]]:
        hub = self.hub
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for room_id, role in hub.store.seats_of(pid):
            out.append(self._summary(hub.room(room_id), role, waiting=False))
            seen.add(room_id)
        email = hub.accounts.require(pid)["email"]
        for inv in hub.store.pending_email_invites(email) if email else []:
            if inv["room"] not in seen:
                out.append(self._summary(hub.room(inv["room"]), None, waiting=True))
                seen.add(inv["room"])
        return out

    def _summary(self, room: RoomService, role: str | None, *, waiting: bool) -> dict[str, Any]:
        info = room.read("room") or {}
        return {
            "id": room.id,
            "title": info.get("title", room.id),
            "studio": info.get("activeCase", ""),
            "seats": [_seat(n) for n in (info.get("people") or {}).values()],
            "role": role,
            "waiting": waiting,
            "owner": self.hub.store.room_owner(room.id),
        }

    # leaving and deleting ---------------------------------------------------------------------
    def leave(self, pid: str, room_id: str) -> dict[str, Any]:
        """Give up your seat. The person who created the room deletes it instead (like a
        repository's owner), so a room always has someone who can remove it."""
        room = self._seated(room_id, pid)
        if self.hub.store.room_owner(room_id) == pid:
            raise Conflict("you created this room: delete it instead, or it would have no owner")
        room.leave(pid)
        self.hub.store.remove_seat(room_id, pid)
        run_leave_hooks(self.hub.store, room_id, pid)
        return {"left": room_id}

    def delete(self, pid: str, room_id: str, confirm: str) -> None:
        """Only the creator, and only with the room's address typed back, deletes a room and
        everything in it. This cannot be undone."""
        self._seated(room_id, pid)
        if self.hub.store.room_owner(room_id) != pid:
            raise Denied("only the person who created this room can delete it")
        if confirm != room_id:
            raise RoomError("type the room's address to confirm")
        self.hub.store.delete_room(room_id, run_hooks=False)
        run_room_delete_hooks(self.hub.store, room_id)
        self.hub.forget_room(room_id)

    # invites ----------------------------------------------------------------------------------
    def new_invite(self, pid: str, room_id: str, email: str | None) -> dict[str, Any]:
        room = self._seated(room_id, pid)
        address = None
        if email is not None:
            address = normalize_email(email)
            if address is None:
                raise RoomError("that is not an email address")
        if len(room.read("room", "people") or {}) >= 4:
            raise Conflict("this room has 4 seats and they are taken", code="room_full")
        if address is not None:
            return self._invite_by_email(room, pid, address)
        made = self.hub.invites.create(room_id, pid, None)
        return {
            "id": made.id,
            "url": self._url("invite", made.token),
            "expiresAt": iso(made.expires),
        }

    def revoke(self, pid: str, room_id: str, invite_id: str) -> None:
        self._seated(room_id, pid)
        inv = self.hub.store.invite(invite_id)
        if inv is None or inv["room"] != room_id:
            raise NotFound("no such invite in this room")
        if inv["created_by"] != pid:
            raise Denied("only the person who made an invite revokes it")
        self.hub.invites.revoke(invite_id)

    def preview(self, pid: str, token: str) -> dict[str, Any]:
        inv = self.hub.invites.lookup(token)
        self._check_email(pid, inv)
        room = self.hub.room(inv["room"])
        info = room.read("room") or {}
        people = info.get("people") or {}
        inviter = people.get(inv["created_by"])
        account = self.hub.store.person(inv["created_by"]) or {}
        return {
            "room": {
                "id": room.id,
                "title": info.get("title", room.id),
                "studio": info.get("activeCase", ""),
                "capabilities": room.snapshot()["room"]["capabilities"],
            },
            "inviter": {
                "id": inv["created_by"],
                "name": (inviter or {}).get("name") or account.get("name", ""),
                "color": _color_name(inviter) if inviter else account.get("color", "blue"),
            },
            "seats": [{**_seat(n), "role": n.get("role")} for n in people.values()],
            "lanes": room.read("lanes") or {},
            "expiresAt": iso(float(inv["expires"])),
            "seated": pid in people,
            "full": len(people) >= 4,
        }

    async def accept(
        self, pid: str, token: str, role: Role, agent: AgentChoiceIn
    ) -> dict[str, Any]:
        hub = self.hub
        spent = hub.store.invite_by_digest(digest_of(token))
        if spent is not None and spent["used_by"] == pid:
            seated = hub.room(spent["room"])
            if seated.read("room", "people", pid) is not None:  # a retried accept: already in
                return {"room": seated.id, "seq": seated.seq}
        inv = hub.invites.lookup(token)
        self._check_email(pid, inv)
        self._require_profile(pid)
        room = hub.room(inv["room"])
        if room.read("room", "people", pid) is not None:
            return {"room": room.id, "seq": room.seq}
        prefs = self._prefs(pid, agent)
        if prefs.card_url is not None:
            await check_card_url(hub.runtime.brought.policy, prefs.card_url)
        single_use = inv["kind"] == "email"
        with room.lock:  # the seat count, the spend and the seat are one step
            if len(room.read("room", "people") or {}) >= 4:
                raise Conflict("this room has 4 seats and they are taken", code="room_full")
            if not hub.store.use_invite(inv["id"], pid, single_use):
                raise RoomError("this invite can no longer be used", code="invite_used")
            try:
                self._seat(room, pid, role, prefs)
            except Exception:
                if single_use:
                    hub.store.release_invite(inv["id"])
                raise
            room.take_role_lanes(pid)  # open ones, and those held while someone worked alone
        out: dict[str, Any] = {"room": room.id, "seq": room.seq}
        error = await self._bring(room, pid, prefs)
        if error:
            out["agentError"] = error
        out["seq"] = room.seq
        return out

    # helpers ----------------------------------------------------------------------------------
    def _url(self, kind: str, token: str) -> str:
        return f"{self.hub.settings.web_origin.rstrip('/')}/{kind}/{token}"

    def _seated(self, room_id: str, pid: str) -> RoomService:
        room = self.hub.room(room_id)
        if room.read("room", "people", pid) is None:
            raise Denied(f"{pid} has no seat in room {room_id}")
        return room

    def _require_profile(self, pid: str) -> None:
        if not self.hub.accounts.require(pid)["profile_complete"]:
            raise Conflict("finish your profile first", code="profile_incomplete")

    def _check_email(self, pid: str, inv: dict[str, Any]) -> None:
        """An email invite is for that address only."""
        if inv["kind"] == "email" and self.hub.accounts.require(pid)["email"] != inv["email"]:
            raise Denied("this invite is for another email address", code="invite_email_mismatch")

    def _clean_emails(self, pid: str, emails: list[str]) -> list[str]:
        mine = self.hub.accounts.require(pid)["email"]
        out: list[str] = []
        for raw in emails:
            address = normalize_email(raw)
            if address is None:
                raise RoomError(f"{raw!r} is not an email address")
            if address != mine and address not in out:
                out.append(address)
        if len(out) > MAX_EMAIL_INVITES:
            raise RoomError(f"a room has 4 seats: invite at most {MAX_EMAIL_INVITES} people")
        return out

    def _invite_by_email(self, room: RoomService, pid: str, email: str) -> dict[str, Any]:
        made = self.hub.invites.create(room.id, pid, email)
        url = self._url("invite", made.token)
        who = self.hub.accounts.require(pid)["name"] or "Someone"
        title = (room.read("room") or {}).get("title", room.id)
        try:
            self.hub.mailer.send(email, f"{who} invited you to {title} on Consilith", url)
        except MailError as exc:
            logger.error("invite mail not sent: %s", exc)
        return {"id": made.id, "url": url, "expiresAt": iso(made.expires)}

    def _prefs(self, pid: str, choice: AgentChoiceIn | None = None) -> AgentPrefs:
        """The person's stored agent choice, with this accept's choice of kind laid over it."""
        prefs = prefs_of(self.hub, pid)
        if choice is None:
            return prefs
        if choice.kind == "brought":
            if not choice.card_url:
                raise RoomError("a brought agent needs its Agent Card URL")
            return prefs.model_copy(update={"kind": "brought", "card_url": choice.card_url})
        return prefs.model_copy(update={"kind": "consilith", "card_url": None})

    def _seat(self, room: RoomService, pid: str, role: Role, prefs: AgentPrefs) -> None:
        stored = self.hub.store.agent_prefs(pid)
        config = seat_config(room.default_role_config(role), prefs if stored is not None else None)
        room.join(pid, role, config=config)

    async def _bring(self, room: RoomService, pid: str, prefs: AgentPrefs) -> str | None:
        """Replace the spawned agent with the one the person brought. The seat stands either
        way: a card that does not pass leaves them with the room's agent, and they are told."""
        if prefs.kind != "brought" or prefs.card_url is None:
            return None
        try:
            await self.hub.runtime.brought.bring(room, pid, prefs.card_url)
        except RoomError as exc:
            return exc.message
        except Exception:
            logger.exception("bringing %s's agent failed", pid)
            return "the agent card could not be read"
        return None


ROOM_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def admission_router(admission: Admission, person: Callable[..., str]) -> APIRouter:
    router = APIRouter()

    @router.post("/rooms", status_code=201)
    async def create_room(body: NewRoomIn, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        if not ROOM_ID.match(body.id):
            raise RoomError("room ids are lowercase letters, digits and dashes")
        return await admission.create_room(
            p, body.id, body.title, body.studio, body.role, body.invite, body.alone
        )

    @router.get("/rooms")
    def rooms(p: Annotated[str, Depends(person)]) -> list[dict[str, Any]]:
        return admission.list_rooms(p)

    @router.post("/rooms/{room_id}/leave")
    def leave(room_id: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        return admission.leave(p, room_id)

    @router.delete("/rooms/{room_id}", status_code=204)
    def delete(room_id: str, confirm: str, p: Annotated[str, Depends(person)]) -> Response:
        admission.delete(p, room_id, confirm)
        return Response(status_code=204)

    @router.post("/rooms/{room_id}/invites", status_code=201)
    def new_invite(
        room_id: str, body: NewInviteIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        return admission.new_invite(p, room_id, body.email)

    @router.delete("/rooms/{room_id}/invites/{invite_id}", status_code=204)
    def revoke(room_id: str, invite_id: str, p: Annotated[str, Depends(person)]) -> Response:
        admission.revoke(p, room_id, invite_id)
        return Response(status_code=204)

    @router.get("/invites/{token}")
    def preview(token: str, p: Annotated[str, Depends(person)]) -> dict[str, Any]:
        return admission.preview(p, token)

    @router.post("/invites/{token}/accept")
    async def accept(
        token: str, body: AcceptIn, p: Annotated[str, Depends(person)]
    ) -> dict[str, Any]:
        return await admission.accept(p, token, body.role, body.agent)

    return router
