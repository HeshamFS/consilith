"""Agent configuration (the drawer), watches, and the ops a brought agent returns."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .adhoc import AdHocPlan
from .base import Contract
from .providers import ProviderId
from .room import PatchOp


class Autonomy(StrEnum):
    OBSERVE = "observe"
    SUGGEST = "suggest"
    EDIT_MY_LANES = "edit_my_lanes"
    RUN_SOLVERS = "run_solvers"


AUTONOMY_ORDER = [Autonomy.OBSERVE, Autonomy.SUGGEST, Autonomy.EDIT_MY_LANES, Autonomy.RUN_SOLVERS]


def at_least(have: Autonomy, need: Autonomy) -> bool:
    return AUTONOMY_ORDER.index(have) >= AUTONOMY_ORDER.index(need)


class Watch(Contract):
    """A rule checked against every result (BRD agent drawer).

    Example: fatigue SF of at least 1.0 is ``Watch(quantity="fatigue_sf", op=">=", threshold=1.0)``.
    """

    id: str
    case: str
    quantity: str
    op: Literal[">=", "<=", ">", "<", "abs<="]
    threshold: float
    unit: str = ""
    on_break: Literal["warn", "propose"] = "warn"
    label: str = ""
    reference: str | None = None  # for abs<=: the quantity it is compared against

    def holds(self, value: float, reference: float | None = None) -> bool:
        match self.op:
            case ">=":
                return value >= self.threshold
            case "<=":
                return value <= self.threshold
            case ">":
                return value > self.threshold
            case "<":
                return value < self.threshold
            case "abs<=":
                ref = 0.0 if reference is None else reference
                return abs(value - ref) <= self.threshold


class AgentConfig(Contract):
    """The per-person agent drawer. Enforced by the room gate and the conductor."""

    skills: list[str] = Field(default_factory=list[str])
    tools_allowed: list[str] = Field(default_factory=list[str])
    tools_denied: list[str] = Field(default_factory=list[str])
    autonomy: Autonomy = Autonomy.EDIT_MY_LANES
    watches: list[Watch] = Field(default_factory=list[Watch])
    goal: str = ""
    compute_budget_s: float = Field(default=60.0, gt=0)
    # A name from the provider registry (providers.py); an unknown name is refused.
    model_provider: ProviderId = "scripted"
    model_name: str | None = None

    def tool_allowed(self, tool: str) -> bool:
        if tool in self.tools_denied:
            return False
        return not self.tools_allowed or tool in self.tools_allowed


class AgentKind(StrEnum):
    PERSONAL = "personal"
    ROOM = "room"


class AgentEdit(Contract):
    case: str
    patch: list[PatchOp] = Field(min_length=1)
    reason: str = Field(min_length=1)


class AgentProposalOp(Contract):
    case: str
    target_lane: str
    patch: list[PatchOp] = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list[str])


class AgentWarningOp(Contract):
    case: str
    lane: str
    to: str
    message: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list[str])


class AgentOpCall(Contract):
    """One platform op a brought agent asks for. It runs through ToolHost under the agent's own
    autonomy and the profile of the message it answers, so the same checks apply as for a model.
    """

    op: str = Field(min_length=1, max_length=100)
    args: dict[str, Any] = Field(default_factory=dict[str, Any])


class AgentOps(Contract):
    """What a brought agent returns, as an A2A Part(data=...) with this media type."""

    edits: list[AgentEdit] = Field(default_factory=list[AgentEdit])
    proposals: list[AgentProposalOp] = Field(default_factory=list[AgentProposalOp])
    warnings: list[AgentWarningOp] = Field(default_factory=list[AgentWarningOp])
    message: str | None = None
    # Platform ops, each run through ToolHost. Which ops are allowed depends on what the agent is
    # answering: its owner's chat, a room event, or a peer message.
    ops: list[AgentOpCall] = Field(default_factory=list[AgentOpCall])
    # A refs-only plan for the owner's chat. No numbers, no approve or run controls.
    surface: AdHocPlan | None = None


OPS_MEDIA_TYPE = "application/vnd.consilith.ops+json"
EVENT_MEDIA_TYPE = "application/vnd.consilith.event+json"
