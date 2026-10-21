"""What the room asks the referee to check, and the media types on the A2A wire."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import Contract
from .results import ArtifactRef, OutputPayload, RunManifest, Tier

REVIEW_MEDIA_TYPE = "application/vnd.consilith.review+json"
LEDGER_MEDIA_TYPE = "application/vnd.consilith.ledger+json"
PROPOSAL_MEDIA_TYPE = "application/vnd.consilith.proposal+json"
DECISION_MEDIA_TYPE = "application/vnd.consilith.decision+json"
REGISTRY_MEDIA_TYPE = "application/vnd.consilith.registry+json"
SWARM_MEDIA_TYPE = "application/vnd.consilith.swarm+json"


class ReviewItem(OutputPayload):
    result_id: str
    quantity: str
    result_digest: str
    input_revisions: dict[str, str]
    manifest_digest: str
    tier: Tier
    artifacts: list[ArtifactRef] = Field(
        default_factory=list[ArtifactRef], exclude_if=lambda value: not value
    )


class ReviewRequest(Contract):
    room: str
    case: str
    run_id: str
    manifest: RunManifest
    items: list[ReviewItem] = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict[str, Any])  # e.g. {"alloy": "6061-T6"}
