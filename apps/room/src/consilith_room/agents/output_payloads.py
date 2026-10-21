"""Normalize producer outputs and bytes identically for storage and replay."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from consilith_contracts import ArtifactRef, ResultEnvelope, canonical_bytes
from consilith_contracts.results import ArtifactOutput, NumberOutput, OutputKind
from pydantic import JsonValue

from .artifact_projection import ArtifactProjection


@dataclass(frozen=True)
class PreparedOutput:
    name: str
    kind: OutputKind
    value: JsonValue
    unit: str
    artifacts: list[ArtifactRef]


def prepare_outputs(
    envelope: ResultEnvelope,
    presenter: ArtifactProjection | Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[list[PreparedOutput], dict[str, tuple[ArtifactRef, bytes]]]:
    blobs: dict[str, tuple[ArtifactRef, bytes]] = {}

    def bind(data: bytes, media_type: str, role: str, kind: str = "file") -> ArtifactRef:
        ref = ArtifactRef(
            sha256=hashlib.sha256(data).hexdigest(),
            media_type=media_type,
            size_bytes=len(data),
            role=role,
            kind=kind,
        )
        blobs[ref.sha256] = (ref, data)
        return ref

    common: list[ArtifactRef] = []
    if envelope.fields:
        common.append(bind(canonical_bytes(envelope.fields), "application/json", "fields", "json"))
        if presenter is not None:
            display = (
                presenter.project(envelope.fields)
                if isinstance(presenter, ArtifactProjection)
                else presenter(envelope.fields)
            )
            if display is not None:
                common.insert(
                    0, bind(canonical_bytes(display), "application/json", "visualization", "json")
                )
    outputs = [
        PreparedOutput(q.name, "number", q.value, q.unit, list(common)) for q in envelope.quantities
    ]
    for output in envelope.outputs:
        refs = [bind(a.bytes(), a.media_type, a.role) for a in output.artifacts]
        outputs.append(
            PreparedOutput(
                output.name,
                output.kind,
                None if isinstance(output, ArtifactOutput) else output.value,
                output.unit if isinstance(output, NumberOutput) else "",
                refs + common,
            )
        )
    return outputs, blobs
