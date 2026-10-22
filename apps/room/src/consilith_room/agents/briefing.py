"""The per-turn briefing (section 9): a deterministic, server-built view of the room for one
person, with no model in the loop and no other person's free text.

Each section is stamped ``asOfSeq`` (the room's sequence number when it was built). The briefing
carries other people's items by id, author id, lane and age only. Display names, room titles,
provider names and model names never appear. Class-B text (proposal reasons, decision reasons,
warning messages, lesson reasons) is fetched through read ops, which fence it and set taint.

Audiences: ``owner`` is the person's own chat. ``peer`` is for text from another agent: no
personal lessons, no goal or watches, no inbox and no chat history. ``event`` is for a brought
agent's room events.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Protocol, cast, runtime_checkable

from consilith_contracts import (
    Autonomy,
    Lesson,
    MemoryScope,
    ResultRecord,
    TurnRole,
    at_least,
)

from ..domain.actors import agent_id
from ..domain.service import RUN_ACTIVE, RoomService
from .context_budget import estimate_tokens

Audience = Literal["owner", "peer", "event"]

# Caps from the section 9 table.
ME_TOKENS = 400
SEATS = 4
LANE_ROW_TOKENS = 25
TWIN_LANE_CHARS = 1_500
RESULT_ROWS = 40
RUN_ROWS = 10
FAILURE_ROWS = 3
FAILURE_TEXT_CHARS = 200
INBOX_ROWS = 10
DELTA_TOKENS = 600
DELTA_COUNT_OVER = 30
LESSON_LINES = 20
PERSONAL_LESSONS = 10
ROOM_LESSONS = 5
CASE_LESSONS = 5
TARGET_TOKENS = 2_500
HARD_CAP_TOKENS = 4_000

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:/\-]{1,64}$")
# Log meta keys whose values are ids or fixed words. Anything else (titles, messages, reasons)
# is left out of the one-liners.
_META_KEYS = (
    "run",
    "results",
    "proposal",
    "warning",
    "tension",
    "lesson",
    "scope",
    "case",
    "lane",
    "status",
    "tool",
    "accept",
    "job",
    "role",
    "to",
)
_QUIET_KINDS = frozenset({"agent.status"})
_DONE_TENSION = frozenset({"resolved", "decided", "blocked", "closed", "cancelled"})


@runtime_checkable
class BriefingPort(Protocol):
    """Anything that builds a briefing the way ``build_briefing`` does."""

    def __call__(self, room: RoomService, person: str, audience: Audience) -> dict[str, Any]: ...


def _ids(value: Any) -> str | None:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str) and _SAFE_ID.match(value):
        return value
    return None


def _cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[: max(chars - 3, 0)] + "..."


# me ------------------------------------------------------------------------------------


def _owned(room: RoomService, person: str, case: str) -> list[str]:
    return [ln for ln, owner in room.lane_owners().get(case, {}).items() if owner == person]


def _me(room: RoomService, person: str, audience: Audience, case: str) -> dict[str, Any]:
    aid = agent_id(person)
    node = room.agent(aid)
    cfg = room.agent_config(aid)
    auto = cfg.autonomy
    mine = _owned(room, person, case)
    if not at_least(auto, Autonomy.EDIT_MY_LANES):
        edit = {
            "allowed": False,
            "reason": f"autonomy is {auto.value}; editing needs edit_my_lanes",
        }
    elif not mine:
        edit = {"allowed": False, "reason": "you own no lane in the active case"}
    else:
        edit = {"allowed": True, "reason": ""}
    if at_least(auto, Autonomy.SUGGEST):
        propose = {"allowed": True, "reason": ""}
    else:
        propose = {"allowed": False, "reason": f"autonomy is {auto.value}; proposing needs suggest"}
    run: dict[str, Any]
    if not at_least(auto, Autonomy.EDIT_MY_LANES):
        run = {
            "allowed": False,
            "reason": f"autonomy is {auto.value}; a run needs edit_my_lanes and your confirm",
        }
    elif not room.case_ready(case):
        run = {"allowed": False, "reason": "the case is locked until every lane is owned"}
    else:
        run = {"allowed": True, "reason": "", "needsConfirm": auto != Autonomy.RUN_SOLVERS}
    if case:  # what run.start takes: the capability id (never a tool name) and its modes
        run.update(_runnable(case))
    role = (room.read("room", "people", person) or {}).get("role", "")
    out: dict[str, Any] = {
        "asOfSeq": room.seq,
        "person": person,
        "role": role,
        "agent": aid,
        "agentStatus": node.get("status", "idle"),
        "autonomy": auto.value,
        "canEdit": edit,
        "canPropose": propose,
        "canRun": run,
        "toolsAllowed": list(cfg.tools_allowed)[:20],
        "toolsDenied": list(cfg.tools_denied)[:20],
    }
    if audience != "peer":  # the person's own words (class A); a peer turn does not need them
        out["goal"] = _cut(cfg.goal, 500)
        out["watches"] = [
            {
                "id": w.id,
                "case": w.case,
                "quantity": w.quantity,
                "op": w.op,
                "threshold": w.threshold,
                "unit": w.unit,
                "onBreak": w.on_break,
            }
            for w in cfg.watches[:10]
        ]
    return _fit_me(out)


def _runnable(case: str) -> dict[str, Any]:
    from consilith_contracts.extensions import optional_extension

    modes = optional_extension("queue_modes", case)
    default = optional_extension("default_modes", case)
    listed = [str(m) for m in modes][:8] if isinstance(modes, list | tuple) else []
    if isinstance(default, str) and default not in listed:
        listed.insert(0, default)
    out: dict[str, Any] = {"capability": case, "modes": listed}
    if isinstance(default, str):
        out["defaultMode"] = default
    return out


def _fit_me(me: dict[str, Any]) -> dict[str, Any]:
    while estimate_tokens(json.dumps(me)) > ME_TOKENS:
        if me.get("watches"):
            me["watches"] = me["watches"][:-1]
        elif me.get("goal"):
            me["goal"] = _cut(str(me["goal"]), len(str(me["goal"])) // 2)
        elif len(me["toolsAllowed"]) > 5 or len(me["toolsDenied"]) > 5:
            me["toolsAllowed"], me["toolsDenied"] = me["toolsAllowed"][:5], me["toolsDenied"][:5]
        else:
            break
    return me


# others, lanes, twin ---------------------------------------------------------------------


def _others(room: RoomService, person: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    people = cast(dict[str, Any], room.read("room", "people") or {})
    for pid in sorted(people):
        if pid == person:
            continue
        agent = cast(dict[str, Any], room.read("room", "agents", agent_id(pid)) or {})
        rows.append(
            {
                "person": pid,
                "role": people[pid].get("role", ""),
                "agent": agent_id(pid),
                "agentStatus": agent.get("status", "idle"),
                "brought": bool(agent.get("brought")),
            }
        )
    return {"asOfSeq": room.seq, "seats": rows[:SEATS]}


def _lanes(room: RoomService, person: str) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for case, owners in sorted(room.lane_owners().items()):
        rows = [
            {"lane": ln, "owner": owner, "mine": owner == person}
            for ln, owner in sorted(owners.items())
        ]
        cases[case] = {
            "caseReady": room.case_ready(case),
            "open": [r["lane"] for r in rows if r["owner"] is None],
            "lanes": rows,
        }
    out = {"asOfSeq": room.seq, "cases": cases}
    # 25 tokens a lane: drop the per-lane rows of inactive cases if the section runs over.
    active = str(room.read("room", "activeCase") or "")
    lanes_total = sum(len(c["lanes"]) for c in cases.values())
    while estimate_tokens(json.dumps(out)) > LANE_ROW_TOKENS * max(lanes_total, 1) + 60:
        victim = next((k for k in cases if k != active and cases[k]["lanes"]), None)
        if victim is None:
            break
        cases[victim]["lanes"] = []
    return out


def _twin(room: RoomService, person: str, case: str) -> dict[str, Any]:
    owners = room.lane_owners().get(case, {})
    lanes: dict[str, Any] = {}
    for ln in sorted(owners):
        value = cast(dict[str, Any], room.read("twin", case, ln) or {})
        rev = room.read("revisions", case, ln)
        if owners[ln] == person:
            text = json.dumps(value, sort_keys=True)
            if len(text) <= TWIN_LANE_CHARS:
                lanes[ln] = {"mine": True, "revision": rev, "fields": value}
            else:
                lanes[ln] = {
                    "mine": True,
                    "revision": rev,
                    "fieldNames": sorted(value),
                    "cut": True,
                }
        else:  # others' lanes: field names and pointers only, never their values
            lanes[ln] = {
                "mine": False,
                "owner": owners[ln],
                "revision": rev,
                "fieldNames": sorted(value),
                "pointers": [f"/twin/{case}/{ln}/{k}" for k in sorted(value)],
            }
    return {"asOfSeq": room.seq, "case": case, "lanes": lanes}


# results, runs ---------------------------------------------------------------------------


def _stale_reason(room: RoomService, rec: ResultRecord) -> str:
    try:
        if room.run_is_current(rec.run_id):
            return "a later run failed"
    except Exception:  # the run may be gone; the reason stays honest and generic
        return "inputs changed"
    return "an input lane changed after the run"


def _results(room: RoomService, case: str, rows: int) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    for node in cast(dict[str, Any], room.read("results") or {}).values():
        rec = ResultRecord.model_validate(node)
        if rec.case != case or rec.status.value not in ("current", "stale"):
            continue
        row: dict[str, Any] = {
            "id": rec.id,
            "quantity": rec.quantity,
            "kind": rec.kind,
            "unit": rec.unit,
            "stamp": rec.stamp.value,
            "tier": int(rec.tier),
            "status": rec.status.value,
            "inside": rec.validity.inside,
            "ledger": rec.ledger_id,
        }
        if rec.status.value == "stale":
            row["reason"] = _stale_reason(room, rec)
        out.append(row)
    out.sort(key=lambda r: r["status"] != "current")  # stale rows go last, then drop first
    return {"asOfSeq": room.seq, "case": case, "total": len(out), "rows": out[:rows]}


def _runs(room: RoomService) -> dict[str, Any]:
    runs = cast(dict[str, dict[str, Any]], room.read("runs") or {})
    active = [
        {
            "id": rid,
            "case": r.get("case"),
            "tool": r.get("tool"),
            "tier": r.get("tier"),
            "state": "running",
        }
        for rid, r in runs.items()
        if r.get("status") == RUN_ACTIVE
    ]
    queued = [
        {"id": jid, "case": q.get("case"), "mode": q.get("mode"), "state": q.get("status")}
        for jid, q in cast(dict[str, dict[str, Any]], room.read("queue") or {}).items()
        if q.get("status") in ("queued", "running")
    ]
    failed = [
        (int(r.get("openedSeq", 0)), rid, r)
        for rid, r in runs.items()
        if r.get("status") in ("failed", "timeout")
    ]
    failed.sort(key=lambda t: t[0], reverse=True)
    failures = [
        {
            "id": rid,
            "case": r.get("case"),
            "tool": r.get("tool"),
            "status": r.get("status"),
            "error": _cut(str(r.get("error", "")), FAILURE_TEXT_CHARS),
        }
        for _s, rid, r in failed[:FAILURE_ROWS]
    ]
    return {"asOfSeq": room.seq, "active": (active + queued)[:RUN_ROWS], "failures": failures}


# inbox -----------------------------------------------------------------------------------


def _inbox(room: RoomService, person: str) -> dict[str, Any]:
    seq = room.seq
    awaiting = [
        {
            "id": p.id,
            "author": p.author,
            "lane": f"{p.case}/{p.target_lane}",
            "age": max(seq - p.created_seq, 0),
        }
        for p in room.open_proposals(owner=person)
    ]
    mine = [
        {"id": p.id, "lane": f"{p.case}/{p.target_lane}", "owner": p.lane_owner}
        for p in room.open_proposals()
        if p.on_behalf_of == person and p.lane_owner != person
    ]
    warnings = [
        {"id": wid, "kind": "warning", "lane": f"{w.get('case')}/{w.get('lane')}"}
        for wid, w in cast(dict[str, dict[str, Any]], room.read("warnings") or {}).items()
        if w.get("to") == person and not w.get("acknowledged")
    ]
    tensions = [
        {"id": tid}
        for tid, t in cast(dict[str, dict[str, Any]], room.read("tensions") or {}).items()
        if t.get("status") not in _DONE_TENSION
    ]
    return {
        "asOfSeq": seq,
        "proposalsForMe": awaiting[:INBOX_ROWS],
        "myProposalsWaiting": mine[:INBOX_ROWS],
        "warnings": warnings[:INBOX_ROWS],
        "tensions": tensions[:INBOX_ROWS],
        "peerThreads": [],  # WS-P fills this from the thread index (id, count)
    }


# delta -----------------------------------------------------------------------------------


def last_seen_seq(room: RoomService, person: str) -> int:
    """The room seq the person's last answered turn was briefed at (``meta.asOfSeq``)."""
    # Tool rows sit between the person's own rows, so read user rows only, further back if need be.
    for turn in reversed(room.chat_rows(person, (TurnRole.USER, TurnRole.AGENT), 40)):
        seen = turn.meta.get("asOfSeq")
        if isinstance(seen, int) and not isinstance(seen, bool):
            return seen
    return 0


def _line(row: Any) -> str:
    parts = [f"#{row.seq} {row.kind} by {row.actor}"]
    for key in _META_KEYS:
        value = row.meta.get(key)
        if isinstance(value, list):
            shown = [s for s in (_ids(v) for v in cast(list[Any], value)[:5]) if s]
            if shown:
                parts.append(f"{key}={','.join(shown)}")
        elif (s := _ids(value)) is not None:
            parts.append(f"{key}={s}")
    return " ".join(parts)


def _delta(room: RoomService, person: str) -> dict[str, Any]:
    after = last_seen_seq(room, person)
    rows = [r for r in room.store.log(room.id, after=after) if r.kind not in _QUIET_KINDS]
    out: dict[str, Any] = {"asOfSeq": room.seq, "since": after}
    if len(rows) > DELTA_COUNT_OVER:
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.kind] = counts.get(r.kind, 0) + 1
        out["counts"] = dict(sorted(counts.items()))
        return out
    lines = [_line(r) for r in rows]
    while lines and estimate_tokens("\n".join(lines)) > DELTA_TOKENS:
        lines.pop(0)  # the oldest go first
    out["lines"] = lines
    return out


# plugins, memory -------------------------------------------------------------------------


def _plugins(room: RoomService) -> dict[str, Any]:
    rows = [
        {
            "id": p.get("id"),
            "capabilities": len(p.get("capabilities", [])),
            "roles": len(p.get("roles", [])),
            "tools": len(p.get("tools", [])),
            "skills": len(p.get("skills", [])),
        }
        for p in cast(list[dict[str, Any]], room.read("room", "plugins") or [])
    ]
    return {"asOfSeq": room.seq, "plugins": rows}


def _headline(room: RoomService, les: Lesson) -> str:
    """A platform-generated line for a lesson; the reason text is only in ``lessons.recall``."""
    dec = cast("dict[str, Any] | None", room.read("decisions", les.source_decision or ""))
    if dec is not None:
        prop = cast(dict[str, Any], room.read("proposals", str(dec.get("proposal"))) or {})
        verb = "accepted" if dec.get("accept") else "declined"
        lane = f"{prop.get('case')}/{prop.get('targetLane')}"
        return f"edit to lane {lane} {verb} by {dec.get('by')}"
    return f"{les.scope.value} lesson {les.id} by {les.taught_by}"


def _memory(room: RoomService, person: str, audience: Audience, case: str) -> dict[str, Any]:
    aid = agent_id(person)
    personal: list[Lesson] = []
    shared: list[Lesson] = []
    for les in room.lessons_for(aid):
        if les.scope == MemoryScope.PERSONAL:
            # A personal lesson counts here only if the decision behind it was made in this room.
            if audience != "peer" and room.read("decisions", les.source_decision or "") is not None:
                personal.append(les)
        else:
            shared.append(les)
    room_scope = [x for x in shared if x.scope == MemoryScope.ROOM]
    holder = f"{room.id}:{case}"
    case_scope = [x for x in shared if x.scope == MemoryScope.CASE and x.holder == holder]
    chosen = (
        [("personal", x) for x in personal[:PERSONAL_LESSONS]]
        + [("room", x) for x in room_scope[:ROOM_LESSONS]]
        + [("case", x) for x in case_scope[:CASE_LESSONS]]
    )[:LESSON_LINES]
    return {
        "asOfSeq": room.seq,
        "ruleFlags": [{"lesson": x.id, "rule": x.rule} for _s, x in chosen if x.rule is not None],
        "lessons": [{"id": x.id, "scope": s, "headline": _headline(room, x)} for s, x in chosen],
    }


# the whole briefing ----------------------------------------------------------------------


def fit_briefing(briefing: dict[str, Any]) -> dict[str, Any]:
    """Shrink a briefing that runs over the hard cap, in the section 9 drop order: results
    beyond the top rows, then twin detail, then the delta, plugin counts and memory."""

    def size() -> int:
        return estimate_tokens(json.dumps(briefing, sort_keys=True))

    if size() <= HARD_CAP_TOKENS:
        return briefing
    for keep in (20, 10, 5, 0):
        rows = briefing["results"]["rows"]
        briefing["results"]["rows"] = rows[:keep]
        briefing["results"]["cut"] = True
        if size() <= HARD_CAP_TOKENS:
            return briefing
    for lane in briefing["twin"]["lanes"].values():
        lane.pop("fields", None)
        lane["cut"] = True
    for name in ("delta", "plugins", "memory"):
        if size() <= HARD_CAP_TOKENS:
            break
        briefing[name] = {"asOfSeq": briefing["asOfSeq"], "cut": True}
    return briefing


def build_briefing(room: RoomService, person: str, audience: Audience) -> dict[str, Any]:
    """The server-built briefing for one person and audience, stamped ``asOfSeq``."""
    with room.lock:  # one consistent read: every section sees the same seq
        case = str(room.read("room", "activeCase") or "")
        briefing: dict[str, Any] = {
            "asOfSeq": room.seq,
            "audience": audience,
            "room": room.id,
            "activeCase": case,
            "me": _me(room, person, audience, case),
            "others": _others(room, person),
            "lanes": _lanes(room, person),
            "twin": _twin(room, person, case),
            "results": _results(room, case, RESULT_ROWS),
            "runs": _runs(room),
            "delta": _delta(room, person),
            "plugins": _plugins(room),
            "memory": _memory(room, person, audience, case),
        }
        if audience != "peer":
            briefing["inbox"] = _inbox(room, person)
        return fit_briefing(briefing)
