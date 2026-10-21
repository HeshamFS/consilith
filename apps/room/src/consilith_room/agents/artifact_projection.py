"""Validated shallow artifact field bindings; no expressions or numerical computation."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtifactProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fields: dict[str, str] = Field(min_length=1, max_length=32)
    constants: dict[str, str] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def validate_bindings(self) -> ArtifactProjection:
        names = [*self.fields, *self.fields.values(), *self.constants]
        if any(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) is None for name in names):
            raise ValueError("artifact bindings require shallow field names")
        if self.fields.keys() & self.constants.keys():
            raise ValueError("artifact field and constant outputs collide")
        if any(len(value) > 200 for value in self.constants.values()):
            raise ValueError("artifact display constants are too long")
        return self

    def project(self, source: dict[str, Any]) -> dict[str, Any] | None:
        if any(name not in source for name in self.fields.values()):
            return None
        return {**{target: source[name] for target, name in self.fields.items()}, **self.constants}
