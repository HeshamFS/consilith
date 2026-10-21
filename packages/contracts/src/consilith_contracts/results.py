"""Solver results: the envelope every MCP tool returns, and what the room stores.

FR-PR-5: units, a provenance id, the solver version and the wall time on every result.
FR-RG-3: every solver declares its validity range. FR-RG-7: runs replay from a manifest.
"""

from __future__ import annotations

import base64
import binascii
import math
from enum import IntEnum, StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from .base import Contract, digest
from .extensions import PackagePin
from .plugins import ReplayPolicy
from .units import UnitError, parse


class Tier(IntEnum):
    HAND_CALC = 0
    QUICK = 1
    ACCURATE = 2


class Stamp(StrEnum):
    VERIFIED = "verified"
    CHECKING = "checking"
    WARNING = "warning"
    BLOCKED = "blocked"


class ResultStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"  # an input lane changed after the solve, or a later solve failed
    SUPERSEDED = "superseded"  # a newer or higher-tier result replaced it
    FAILED = "failed"
    TIMEOUT = "timeout"


class QuantityOut(Contract):
    """One named number a solver produced, with its unit."""

    name: str = Field(min_length=1)
    value: float
    unit: str

    @field_validator("unit")
    @classmethod
    def _unit_parses(cls, v: str) -> str:
        try:
            parse(v)
        except UnitError as exc:
            raise ValueError(str(exc)) from exc
        return v


class Validity(Contract):
    """The range a solver is valid in, and whether this result is inside it."""

    range: str = Field(min_length=1)
    inside: bool
    reason: str = ""


class Provenance(Contract):
    tool: str = Field(min_length=1)
    solver_version: str = Field(min_length=1)
    inputs_hash: str = Field(min_length=8)
    seed: int | None = None
    wall_time_s: float = Field(ge=0)
    size: dict[str, int | float | str] = Field(
        default_factory=dict[str, int | float | str]
    )  # mesh elements, atoms...
    model: str | None = None  # e.g. the pinned potential or vision model


class ArtifactRef(Contract):
    """Content address, never an arbitrary URL to execute or fetch with server credentials."""

    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    kind: str = "file"
    role: str = "output"


class ArtifactContent(Contract):
    """Bounded inline output bytes; consumers never fetch producer-supplied URLs."""

    media_type: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(max_length=22_369_624)
    role: str = Field(default="output", min_length=1, max_length=128)

    @field_validator("content_base64")
    @classmethod
    def _base64(cls, value: str) -> str:
        try:
            data = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("artifact content must be valid base64") from exc
        if len(data) > 16 * 1024 * 1024:
            raise ValueError("artifact content exceeds 16 MiB")
        return value

    def bytes(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class OutputBase(Contract):
    name: str = Field(min_length=1)
    artifacts: list[ArtifactContent] = Field(default_factory=list[ArtifactContent], max_length=16)


class NumberOutput(OutputBase):
    kind: Literal["number"] = "number"
    value: float = Field(allow_inf_nan=False, strict=True)
    unit: str = ""

    @field_validator("unit")
    @classmethod
    def _unit_parses(cls, value: str) -> str:
        try:
            parse(value)
        except UnitError as exc:
            raise ValueError(str(exc)) from exc
        return value


class TextOutput(OutputBase):
    kind: Literal["text"] = "text"
    value: str


class CategoryOutput(OutputBase):
    kind: Literal["category"] = "category"
    value: str


class JsonOutput(OutputBase):
    kind: Literal["json"] = "json"
    value: JsonValue

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        data["value"] = self.value
        return data


class ArtifactOutput(OutputBase):
    kind: Literal["artifact"] = "artifact"
    artifacts: list[ArtifactContent] = Field(min_length=1, max_length=16)


TypedOutput = Annotated[
    NumberOutput | TextOutput | CategoryOutput | JsonOutput | ArtifactOutput,
    Field(discriminator="kind"),
]
OutputKind = Literal["number", "text", "category", "json", "artifact"]


class OutputPayload(Contract):
    """Stored/reviewed payload. Numeric defaults retain historical canonical bytes."""

    kind: OutputKind = Field(default="number", exclude_if=lambda value: value == "number")
    value: JsonValue = None
    unit: str = ""
    artifacts: list[ArtifactRef] = Field(
        default_factory=list[ArtifactRef], exclude_if=lambda value: not value
    )

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.kind == "json":
            data["value"] = self.value
        return data

    @model_validator(mode="after")
    def _typed_payload(self) -> Self:
        if self.kind == "number":
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError("number outputs require a numeric value")
            if not math.isfinite(self.value):
                raise ValueError("number outputs must be finite")
        elif self.unit:
            raise ValueError("only number outputs may have a unit")
        elif self.kind in ("text", "category") and not isinstance(self.value, str):
            raise ValueError("text and category outputs require a string")
        elif self.kind == "artifact" and self.value is not None:
            raise ValueError("artifact outputs have no scalar payload")
        if self.kind == "artifact" and not self.artifacts:
            raise ValueError("artifact outputs require a content-addressed artifact")
        # RFC 8785 rejects NaN/infinity in nested JSON as well.
        digest(self.value)
        return self


class ResultEnvelope(Contract):
    """What every solver tool returns in structuredContent."""

    quantities: list[QuantityOut] = Field(default_factory=list[QuantityOut])
    output_version: Literal["1"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    outputs: list[TypedOutput] = Field(
        default_factory=list[TypedOutput], max_length=128, exclude_if=lambda value: not value
    )
    provenance: Provenance
    validity: Validity
    tier: Tier
    illustrative: bool = False
    fields: dict[str, Any] = Field(
        default_factory=dict[str, Any]
    )  # bulk arrays: stress field, histogram...
    notes: list[str] = Field(default_factory=list[str])

    @model_validator(mode="after")
    def _outputs(self) -> Self:
        if not self.quantities and not self.outputs:
            raise ValueError("at least one output is required")
        if self.outputs and self.output_version != "1":
            raise ValueError("typed outputs require outputVersion '1'")
        names = [q.name for q in self.quantities] + [o.name for o in self.outputs]
        if len(names) != len(set(names)):
            raise ValueError("output names must be unique")
        for output in self.outputs:
            if isinstance(output, JsonOutput):
                digest(output.value)
        if (
            sum(len(a.bytes()) for output in self.outputs for a in output.artifacts)
            > 16 * 1024 * 1024
        ):
            raise ValueError("combined inline artifacts exceed 16 MiB")
        return self


class RunManifest(Contract):
    """Everything needed to replay a run in a fresh process (FR-RG-7, NFR-7)."""

    tool: str
    server: str
    arguments: dict[str, Any]
    spec_revision: str
    input_revisions: dict[str, str]
    seed: int | None = None
    solver_version: str | None = None
    model: str | None = None
    package_pins: list[PackagePin] = Field(
        default_factory=list[PackagePin], exclude_if=lambda value: not value
    )
    capability_version: str | None = None
    schema_digest: str | None = None
    checker_profile: str | None = None
    replay_policy: ReplayPolicy | None = Field(default=None, exclude_if=lambda value: value is None)
    # Inputs a check depends on that are not tool arguments (alloy, source result ids...).
    # They are part of the manifest digest, so a reviewer can trust them.
    context: dict[str, Any] = Field(default_factory=dict[str, Any])
    artifacts: dict[str, str] = Field(
        default_factory=dict[str, str]
    )  # name -> sha256 in the artifact store
    # Solver quantity name -> the name it was stored under, when the room renamed it.
    outputs: dict[str, str] = Field(default_factory=dict[str, str])


class ResultRecord(OutputPayload):
    """One stored typed output, at /results/{id}."""

    id: str
    room: str
    case: str
    run_id: str
    quantity: str
    tier: Tier
    stamp: Stamp = Stamp.CHECKING
    status: ResultStatus = ResultStatus.CURRENT
    illustrative: bool = False
    provenance: Provenance
    validity: Validity
    manifest_digest: str
    input_revisions: dict[str, str]
    result_digest: str
    ledger_id: str | None = None
    replaces: str | None = None  # the result this one superseded (tier 2 over tier 1)
    artifacts: list[ArtifactRef] = Field(
        default_factory=list[ArtifactRef], exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def _artifact_required(self) -> Self:
        if self.kind == "artifact" and not self.artifacts:
            raise ValueError("artifact outputs require a content-addressed artifact")
        return self


def result_binding_digest(
    quantity: str,
    value: JsonValue,
    unit: str,
    run_id: str,
    manifest: str,
    artifacts: list[ArtifactRef] | None = None,
    *,
    kind: OutputKind = "number",
) -> str:
    payload: dict[str, Any] = {
        "quantity": quantity,
        "value": value,
        "unit": unit,
        "run": run_id,
        "manifest": manifest,
    }
    if artifacts:
        payload["artifacts"] = [ref.wire() for ref in artifacts]
    if kind != "number":
        payload["kind"] = kind
    return digest(payload)
