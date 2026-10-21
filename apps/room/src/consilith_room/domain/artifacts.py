"""Artifact access derives from trusted room events and produced result references.

A content hash copied into an editable model is an input request, not proof that the
room owns its bytes. This module deliberately does not inspect arbitrary document data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ..platform.store import Store

if TYPE_CHECKING:
    from .room import RoomCore


def owns_artifact(room: RoomCore, store: Store, sha256: str) -> bool:
    records = cast(dict[str, dict[str, Any]], room.read("results") or {})
    for record in records.values():
        if record.get("room") == room.id and any(
            ref.get("sha256") == sha256
            for ref in cast(list[dict[str, Any]], record.get("artifacts", []))
        ):
            return True
    after = 0
    while True:
        rows = store.log(room.id, after=after, limit=1000)
        for row in rows:
            if (
                row.kind == "artifact.uploaded"
                and row.meta.get("actorKind") == "person"
                and row.meta.get("sha256") == sha256
            ):
                return True
        if len(rows) < 1000:
            return False
        after = rows[-1].seq
