"""The write gate: every twin patch is authorised operation by operation, atomically.

Rules (SPEC "Shared state" and "Patch authorization"):
- only add, replace, remove and test; move and copy are refused (they would read another lane);
- every path must sit at or below /twin/{case}/{lane}/{field};
- a person writes their own lanes; their agent writes them only with enough autonomy;
- anything else in another person's lane is ``lane_not_owned`` (the agent proposes instead);
- room agents and the backend never edit a lane (FR-AG-5).
One refused operation refuses the whole patch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from consilith_contracts import AgentConfig, Autonomy, CaseDef, PatchOp

from .actors import Actor
from .errors import Denied
from .paths import TWIN_OPS, LanePath, twin_lane

Outcome = Literal["allow", "lane_not_owned", "propose_to_owner"]


@dataclass
class GateResult:
    outcome: Outcome
    lanes: list[LanePath] = field(default_factory=list[LanePath])
    blocked: list[LanePath] = field(default_factory=list[LanePath])
    owners: dict[str, str | None] = field(default_factory=dict[str, str | None])
    reason: str = ""


def authorize_twin_patch(
    actor: Actor,
    patch: list[PatchOp],
    lane_owners: dict[str, dict[str, Any]],
    config: AgentConfig | None,
    cases: dict[str, CaseDef] | None = None,
) -> GateResult:
    """Decide a twin patch. Raises Denied; otherwise returns allow, lane_not_owned or
    propose_to_owner (an agent in ``suggest`` mode writing its own owner's lane)."""
    if not patch:
        raise Denied("empty patch")
    if actor.kind in ("room_agent", "backend"):
        raise Denied(
            f"{actor.id} is a room agent: it builds, checks and composes, never edits a lane"
        )
    lanes: list[LanePath] = []
    for op in patch:
        if op.op not in TWIN_OPS:
            raise Denied(f"operation {op.op!r} is not allowed on the twin")
        if op.from_ is not None:
            raise Denied("'from' is not allowed on the twin")
        lp = twin_lane(op.path, cases)
        if lp not in lanes:
            lanes.append(lp)

    owners: dict[str, str | None] = {}
    blocked: list[LanePath] = []
    for lp in lanes:
        owner = lane_owners.get(lp.case, {}).get(lp.lane)
        owners[lp.key] = owner
        if owner is None:
            raise Denied(f"lane {lp.key} is open: it needs an owner before anyone edits it")
        if actor.person_id != owner:
            blocked.append(lp)
    if blocked:
        return GateResult(
            "lane_not_owned",
            lanes,
            blocked,
            owners,
            reason="lanes owned by someone else: " + ", ".join(lp.key for lp in blocked),
        )
    if actor.kind == "personal_agent":
        cfg = config or AgentConfig()
        if cfg.autonomy == Autonomy.OBSERVE:
            raise Denied(f"{actor.id} is set to observe: it may not write or propose")
        if cfg.autonomy == Autonomy.SUGGEST:
            return GateResult(
                "propose_to_owner", lanes, [], owners, reason="agent is in suggest mode"
            )
    return GateResult("allow", lanes, [], owners)
