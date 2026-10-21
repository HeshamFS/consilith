"""Typed domain errors. The API maps each to one HTTP status and one error body."""

from __future__ import annotations


class RoomError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code


class NotFound(RoomError):
    status = 404
    code = "not_found"


class Denied(RoomError):
    status = 403
    code = "denied"


class LaneNotOwned(RoomError):
    """The write touches a lane its author does not own: make it a proposal (FR-PR-9)."""

    status = 409
    code = "lane_not_owned"

    def __init__(self, message: str, lanes: list[str], owners: dict[str, str | None]) -> None:
        super().__init__(message, lanes=lanes, owners=owners)
        self.lanes = lanes
        self.owners = owners


class Conflict(RoomError):
    status = 409
    code = "conflict"


class RoomFull(Conflict):
    """A room has four seats at most."""

    code = "room_full"


class Locked(RoomError):
    """The room's next step stays locked until every lane is owned (FR-MP-1)."""

    status = 423
    code = "locked"


class SpecInvalid(RoomError):
    """A spec failed validation; ``field`` is the path that failed (FR-LOOP-1)."""

    status = 422
    code = "spec_invalid"

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}", field=field)
        self.field = field


class InvalidLink(RoomError):
    """A one-time link or ticket is expired, used, wrong, or for something else."""

    code = "invalid_link"

    def __init__(self, code: str = "invalid_link") -> None:
        super().__init__("this link is expired, used or not valid; ask for a new one")
        self.code = code


class InviteError(RoomError):
    """An invite that cannot be used. ``reason`` is part of the code, for example
    ``invite_expired``; an unknown token is 404, a spent one is 410."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"this invite is {reason}")
        self.code = f"invite_{reason}"
        self.status = 404 if reason == "unknown" else 410
