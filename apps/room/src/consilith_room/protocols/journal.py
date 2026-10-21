"""The protocol journal: every AG-UI, A2UI, A2A and MCP message, with sender and receiver.

It feeds Agents at work, Show wiring (journal seq refs) and exact session replay (NFR-8/9).
Credentials are redacted before anything is written.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any, Literal, cast

from ..platform.bus import RoomBus
from ..platform.store import Store

Protocol = Literal["AG-UI", "A2UI", "A2A", "MCP", "ROOM"]
SECRET_KEYS = ("token", "authorization", "api_key", "apikey", "secret", "password", "credential")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in cast(dict[str, Any], value).items():
            if any(s in k.lower().replace("-", "_") for s in SECRET_KEYS):
                out[k] = "[redacted]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in cast(list[Any], value)]
    if isinstance(value, str):
        return _redact_text(value)
    return value


BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")
KEY_VALUE = re.compile(
    r"(?i)\b(token|api[_-]?key|apikey|secret|password|authorization)(\s*[=:]\s*)[^\s,;&\"']+"
)


def _redact_text(text: str) -> str:
    """Strings can carry serialised JSON (AG-UI tool args) or headers: redact those too."""
    stripped = text.strip()
    if stripped[:1] in ("{", "[") and len(stripped) < 1_000_000:
        try:
            return json.dumps(redact(json.loads(stripped)))
        except ValueError:
            pass
    text = BEARER.sub(r"\1[redacted]", text)
    return KEY_VALUE.sub(r"\1\2[redacted]", text)


class Journal:
    def __init__(self, store: Store, bus: RoomBus | None = None) -> None:
        self.store = store
        self.bus = bus

    def record(
        self,
        room: str,
        protocol: Protocol,
        kind: str,
        sender: str,
        receiver: str,
        payload: dict[str, Any] | None = None,
        *,
        viewer: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        refs: dict[str, str] | None = None,
        replay: bool = False,
        notify: bool = True,
        payload_for: Callable[[int], dict[str, Any]] | None = None,
    ) -> int:
        """``payload_for`` builds the payload from the entry's own seq, in the same write."""
        entry: dict[str, Any] = {
            "ts": time.time(),
            "room": room,
            "protocol": protocol,
            "kind": kind,
            "sender": sender,
            "receiver": receiver,
            "payload": redact(payload or {}),
            "refs": refs or {},
            "replay": replay,
        }
        if viewer is not None:
            entry["viewer"] = viewer
        if correlation_id is not None:
            entry["correlationId"] = correlation_id
        if causation_id is not None:
            entry["causationId"] = causation_id
        build = None if payload_for is None else (lambda seq: redact(payload_for(seq)))
        seq = self.store.journal_append(room, entry, build)
        if notify and self.bus is not None and protocol != "AG-UI":
            self.bus.notify(room)
        return seq

    def entries(self, room: str, after: int = 0) -> list[dict[str, Any]]:
        return self.store.journal(room, after)
