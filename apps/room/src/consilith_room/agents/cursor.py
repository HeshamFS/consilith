"""Agent cursors: where an agent is acting in a studio, as ephemeral presence (FR-MP-9).

Every agent tool call goes through ``ToolHost.run_call_full``; once a call has passed its checks,
the host calls ``announce`` with the op and its arguments. The room then publishes one presence
event on the bus (never journaled), and the canvas moves that agent's cursor to the element the
call acts on. The event names a place and an action in words, such as "editing Grain boundary
contrast"; it never carries a value, a binding or any other argument text.

Publishers are registered per store (``attach``), the same way ``peer._GETTERS`` finds rooms, so
the owner loop, peer turns and brought agents all reach the same bus without a reference to it.
"""

from __future__ import annotations

import logging
import time
import weakref
from collections.abc import Callable, Mapping
from typing import Any

from ..domain.actors import agent_id

log = logging.getLogger(__name__)

Publish = Callable[[str, dict[str, Any]], None]
_PUBLISHERS: weakref.WeakKeyDictionary[Any, Publish] = weakref.WeakKeyDictionary()
ACTION_CHARS = 80


def attach(store: Any, publish: Publish) -> None:
    """Send the cursor events of rooms on ``store`` to ``publish(room_id, event)``."""
    _PUBLISHERS[store] = publish


def detach(store: Any) -> None:
    _PUBLISHERS.pop(store, None)


def _labels(room: Any, case: str, lane: str, field: str = "") -> tuple[str, str]:
    """The lane's and the field's labels, or their ids when the case does not know them."""
    cdef = room.case_definitions().get(case)
    ldef = cdef.lane(lane) if cdef is not None else None
    lane_label = (ldef.label if ldef is not None else "") or lane
    fdef = next((f for f in ldef.fields if f.name == field), None) if ldef and field else None
    return lane_label, ((fdef.label if fdef is not None else "") or field)


def _active_case(room: Any) -> str:
    return str(room.read("room", "activeCase") or "")


def focus_of(room: Any, ref: str, args: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """``(case, focus, action)`` for a call that acts on something on the canvas, else None.

    ``focus`` is what the canvas looks up: ``/twin/{case}/{lane}/{field}`` for a field,
    ``/twin/{case}/{lane}`` for a lane, ``run:{case}`` for the run control and
    ``workspace:{case}`` for the studio as a whole."""
    edits = args.get("edits")
    first = edits[0] if isinstance(edits, list) and edits and isinstance(edits[0], dict) else {}
    if ref in ("input.set", "proposal.create") and first:
        case = str(first.get("case") or args.get("case") or "")
        lane = str(first.get("lane") or args.get("target_lane") or "")
        field = str(first.get("field") or "")
        if not (case and lane and field):
            return None
        _, label = _labels(room, case, lane, field)
        more = f" and {len(edits) - 1} more" if isinstance(edits, list) and len(edits) > 1 else ""
        verb = "editing" if ref == "input.set" else "proposing a change to"
        return case, f"/twin/{case}/{lane}/{field}", f"{verb} {label}{more}"
    if ref in ("lane.claim", "lane.release", "lane.read", "warning.raise"):
        case, lane = str(args.get("case") or ""), str(args.get("lane") or "")
        if not (case and lane):
            return None
        lane_label, _ = _labels(room, case, lane)
        verb = {
            "lane.claim": "claiming",
            "lane.release": "releasing",
            "lane.read": "reading",
            "warning.raise": "flagging",
        }[ref]
        return case, f"/twin/{case}/{lane}", f"{verb} the {lane_label} lane"
    if ref == "run.start":
        case = str(args.get("case") or args.get("capability") or "")
        return (case, f"run:{case}", "starting the run") if case else None
    if ref == "run.cancel":
        case = _active_case(room)
        return (case, f"run:{case}", "cancelling a run") if case else None
    if ref == "studio.compose":
        case = str(args.get("case") or "")
        return (case, f"workspace:{case}", "arranging the workspace") if case else None
    return None


def announce(room: Any, person: str, ref: str, args: Mapping[str, Any]) -> None:
    """Move ``person``'s agent's cursor to what this call acts on. Never raises."""
    publish = _PUBLISHERS.get(room.store)
    if publish is None:
        return
    try:
        where = focus_of(room, ref, args)
        if where is None:
            return
        case, focus, action = where
        publish(
            room.id,
            {
                "person": agent_id(person),
                "agent": True,
                "owner": person,
                "case": case,
                "focus": focus,
                "action": action[:ACTION_CHARS],
                "ts": time.time(),
            },
        )
    except Exception:  # a cursor is decoration: it never fails a call
        log.exception("agent cursor for %s failed", ref)
