"""Shared model base and canonical hashing.

Every contract crosses a process boundary, so models forbid unknown keys and serialise
in camelCase, the shape the BRD's JSON examples use.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

import rfc8785
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    def wire(self) -> dict[str, Any]:
        """The JSON-ready dict, camelCase, with unset optionals left out."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def canonical_bytes(value: Any) -> bytes:
    """RFC 8785 (JCS) canonical JSON of a JSON-ready value."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return rfc8785.dumps(value)


def digest(value: Any) -> str:
    """sha256 over the canonical JSON, hex."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def new_id(prefix: str) -> str:
    """A short unique id such as ``res-3f9a1c2b7e``."""
    return f"{prefix}-{secrets.token_hex(5)}"
