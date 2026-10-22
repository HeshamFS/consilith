"""Confirm cards: approvals that are not Proposal rows.

A Proposal cannot carry a run or a claim: it needs a patch and one lane, and deciding it demands
a reason and writes a lesson. A confirm card is a ``PendingAction``, private to the person it is
for, bound by a digest to the exact call, the lane revisions it reads and the plugin pins, and
resolved with ``{approved}`` and an optional reason. Everything on a card is built by the server
from the stored call. Model text never becomes card content, except as a short note labelled
untrusted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, StrictBool, ValidationError, field_validator, model_validator

from .base import Contract, digest
from .extensions import PackagePin

# Pending cards expire 15 minutes after they are made.
PENDING_ACTION_TTL_S = 15 * 60
# The interrupt kind of a Proposal awaiting its lane owner. The confirm kinds are below.
INTERRUPT_PROPOSAL = "proposal"


class PendingActionKind(StrEnum):
    CONFIRM_RUN = "confirm_run"
    CONFIRM_CLAIM = "confirm_claim"
    CONFIRM_PEER = "confirm_peer"
    CONFIRM_LESSON = "confirm_lesson"
    CONFIRM_EDIT = "confirm_edit"
    CONFIRM_PIN = "confirm_pin"


class PendingActionStatus(StrEnum):
    PENDING = "pending"
    EXECUTING = "executing"  # claimed by one approver: the compare-and-set from pending
    DONE = "done"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"  # the person sent a new message
    EXPIRED = "expired"

    @property
    def final(self) -> bool:
        return self not in (PendingActionStatus.PENDING, PendingActionStatus.EXECUTING)


class PendingAction(Contract):
    """One row of ``pending_actions``. Lookup is always by ``(room, person, id)``.

    Column names in the store: ``call`` is ``call_json`` and ``result`` is ``result_json``.
    Times are epoch seconds, as everywhere in the store.
    """

    id: str = Field(min_length=1)
    room: str = Field(min_length=1)
    person: str = Field(min_length=1)  # the owner the card is for, never another viewer
    msg_id: str = Field(min_length=1)  # the chat message whose turn made the card
    kind: PendingActionKind
    # The model's call as validated: tool alias and binding-form arguments.
    call: dict[str, Any]
    # The concrete values the host resolved from the bindings. What runs is exactly this.
    resolved_args: dict[str, Any] = Field(default_factory=dict[str, Any])
    # SHA-256 over the canonical JSON of kind, room, person, target, resolved arguments, lane
    # revisions and plugin pins (see ``pending_action_digest``).
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    # Lane path (``case/lane``) to the revision digest the call reads.
    input_revisions: dict[str, str] = Field(default_factory=dict[str, str])
    plugin_pins: list[PackagePin] = Field(default_factory=list[PackagePin])
    # Whether the turn had read class-B text when the card was made. It survives the resume.
    taint: bool = False
    # ``owner``: made in the person's own turn, so the loop continues on approval.
    # ``event``: made for a brought agent's reply to a room event, executed as the single call.
    origin: Literal["owner", "event"] = "owner"
    status: PendingActionStatus = PendingActionStatus.PENDING
    result: dict[str, Any] | None = None
    created: float = Field(ge=0)
    expires: float = Field(ge=0)

    @model_validator(mode="after")
    def expires_after_created(self) -> PendingAction:
        if self.expires <= self.created:
            raise ValueError("a card expires after it is made")
        return self


def pending_action_digest(
    *,
    kind: PendingActionKind | str,
    room: str,
    person: str,
    ref: str,
    mode: str = "",
    resolved_args: Mapping[str, Any],
    input_revisions: Mapping[str, str],
    plugin_pins: Sequence[PackagePin],
) -> str:
    """The digest that binds an approval to one resolved call.

    ``ref`` is the tool ref, or for a run the capability, with ``mode`` beside it. The server
    recomputes this when the card is approved; if lane revisions or plugin pins moved, the digest
    differs and the card is refused.
    """
    return digest(
        {
            "kind": str(kind),
            "room": room,
            "person": person,
            "ref": ref,
            "mode": mode,
            "args": dict(resolved_args),
            "revisions": dict(input_revisions),
            "pins": sorted((p.wire() for p in plugin_pins), key=lambda w: str(w["entryPoint"])),
        }
    )


class ProposalApproval(Contract):
    """A proposal awaiting its lane owner is decided with ``{approved, reason}``; the reason is
    required because the decision is recorded for the author and becomes a lesson."""

    approved: StrictBool
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def reason_is_not_blank(cls, reason: str) -> str:
        if not reason.strip():
            raise ValueError("a reason is required")
        return reason


class ConfirmApproval(Contract):
    """A confirm card is decided with ``{approved}``; a reason is optional."""

    approved: StrictBool
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def blank_reason_is_none(cls, reason: str | None) -> str | None:
        return reason if reason and reason.strip() else None


class ApprovalPayloadError(ValueError):
    """A resume payload does not fit its interrupt kind."""


def approval_kinds() -> tuple[str, ...]:
    """Every interrupt kind a person can resolve: ``proposal`` and the confirm kinds."""
    return (INTERRUPT_PROPOSAL, *(k.value for k in PendingActionKind))


def approval_model(kind: str) -> type[ProposalApproval] | type[ConfirmApproval]:
    """The payload model for an interrupt kind. ``ValueError`` for an unknown kind."""
    if kind == INTERRUPT_PROPOSAL:
        return ProposalApproval
    if kind in {k.value for k in PendingActionKind}:
        return ConfirmApproval
    raise ValueError(f"unknown interrupt kind {kind!r}; use one of: {', '.join(approval_kinds())}")


def approval_response_schema(kind: str) -> dict[str, Any]:
    """The ``response_schema`` the interrupt carries, which depends on its kind.

    The proposal schema is the one the session has always sent; the confirm schema leaves the
    reason optional.
    """
    approval_model(kind)  # refuse an unknown kind
    if kind == INTERRUPT_PROPOSAL:
        return {
            "type": "object",
            "properties": {
                "approved": {"type": "boolean"},
                "reason": {"type": "string", "minLength": 1},
            },
            "required": ["approved", "reason"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": {"approved": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["approved"],
        "additionalProperties": False,
    }


def parse_approval(kind: str, payload: object) -> ProposalApproval | ConfirmApproval:
    """Validate a resume payload for its interrupt kind. ``ApprovalPayloadError`` if it does not
    fit. ``ValueError`` for an unknown kind."""
    model = approval_model(kind)
    needs = "{approved, reason}" if kind == INTERRUPT_PROPOSAL else "{approved}"
    if not isinstance(payload, dict):
        raise ApprovalPayloadError(f"a {kind} approval needs {needs}")
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise ApprovalPayloadError(f"a {kind} approval needs {needs}") from None
