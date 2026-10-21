"""Proposals, decisions, warnings, lessons, ledger entries, journal entries."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator

from .base import Contract
from .extensions import PackagePin
from .results import ArtifactRef, OutputPayload, Stamp


class PatchOp(Contract):
    """One RFC 6902 operation. Twin patches allow add, replace, remove and test only."""

    op: Literal["add", "replace", "remove", "test", "move", "copy"]
    path: str
    value: Any = None
    from_: str | None = Field(default=None, alias="from")

    def wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.op in ("add", "replace", "test"):
            out["value"] = self.value
        if self.from_ is not None:
            out["from"] = self.from_
        return out


class ProposalStatus(StrEnum):
    AWAITING_OWNER = "awaiting-owner"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"
    FAILED = "failed"  # accepted, but the patch no longer applies


class Proposal(Contract):
    id: str
    room: str
    author: str  # the agent id, e.g. "researcherb.agent"
    on_behalf_of: str  # the person id
    case: str
    target_lane: str
    lane_owner: str | None
    patch: list[PatchOp] = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list[str])  # result ids
    status: ProposalStatus = ProposalStatus.AWAITING_OWNER
    a2a_task: str | None = Field(default=None, alias="a2aTask")
    interrupt_id: str | None = None
    created_seq: int = 0


class Decision(Contract):
    id: str
    room: str
    proposal: str
    by: str  # always a person
    accept: bool
    reason: str = Field(min_length=1)
    seq: int = 0

    @field_validator("reason")
    @classmethod
    def _one_line(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("a decision needs a one-line reason")
        return v


class WarningFlag(Contract):
    """A flag that changes nothing and pauses nobody (FR-MP-6)."""

    id: str
    room: str
    author: str
    to: str  # the person it is addressed to
    case: str
    lane: str
    message: str
    evidence: list[str] = Field(default_factory=list[str])
    acknowledged: bool = False


class MemoryScope(StrEnum):
    PERSONAL = "personal"
    ROOM = "room"
    CASE = "case"


class Lesson(Contract):
    """What an agent learned from a decision (FR-MEM-1)."""

    id: str
    scope: MemoryScope
    holder: str  # agent id (personal), room id (room) or "{room}:{case}" (case)
    text: str = Field(min_length=1)
    taught_by: str
    reason: str
    source_decision: str | None = None
    ts: float
    rule: dict[str, Any] | None = (
        None  # machine-checkable form, e.g. {"param":"fillet_r","min":5,"unit":"mm"}
    )
    deleted: bool = False


class Check(Contract):
    name: str
    outcome: Literal["pass", "fail", "warn", "skip"]
    detail: str
    value: float | None = None
    tolerance: float | None = None


class LedgerSubject(OutputPayload):
    """What the referee signs. The room checks every field against its stored result."""

    room: str
    case: str
    run_id: str
    result_id: str
    result_digest: str
    input_revisions: dict[str, str]
    manifest_digest: str
    quantity: str
    checks: list[Check]
    stamp: Stamp
    method: str  # the independent method used
    checker_pin: PackagePin | None = None
    artifacts: list[ArtifactRef] = Field(
        default_factory=list[ArtifactRef], exclude_if=lambda value: not value
    )


class LedgerEntry(Contract):
    id: str
    subject: LedgerSubject
    key_id: str
    signature: str  # base64url Ed25519 over canonical_bytes(subject)
    ts: float


class JournalEntry(Contract):
    """One protocol message, for Agents at work, Show wiring and replay (NFR-8, NFR-9)."""

    seq: int
    ts: float
    room: str
    protocol: Literal["AG-UI", "A2UI", "A2A", "MCP", "ROOM"]
    kind: str
    sender: str
    receiver: str
    viewer: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    refs: dict[str, str] = Field(default_factory=dict[str, str])
    payload: dict[str, Any] = Field(default_factory=dict[str, Any])
    replay: bool = False
