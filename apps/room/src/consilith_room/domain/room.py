"""The room aggregate, part one: the log, seats, lanes, agents and twin edits.

Every change is computed on a copy, committed to the log, and only then becomes the room's
document (commit before apply). A room can therefore be rebuilt from its log alone.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from typing import Any, cast

from consilith_contracts import (
    AgentConfig,
    Autonomy,
    CaseDef,
    PatchOp,
    SpecError,
    new_id,
    validate_lane,
)
from consilith_contracts.extensions import all_extensions, default_case, registry_operation

from ..platform.store import DuplicateOp, LogRow, Store
from .actors import Actor, agent_id
from .document import (
    Doc,
    Patch,
    activation_patch,
    apply,
    empty_document,
    get,
    lane_revision,
    snapshot_catalog,
)
from .errors import Conflict, Denied, LaneNotOwned, Locked, NotFound, RoomFull, SpecInvalid
from .gate import authorize_twin_patch
from .paths import LanePath, pointer
from .roles import COLORS, Role

OnCommit = Callable[[LogRow], None]
MAX_SEATS = 4


def locked[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Hold the room lock across every read, check and commit of a mutation, so no check
    can go stale before its commit (no time-of-check to time-of-use gap)."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        room = cast("RoomCore", args[0])
        with room.lock:
            return fn(*args, **kwargs)

    return wrapper


AGENT_STATUSES = ("idle", "thinking", "working", "delegating", "waiting", "paused")


class RoomCore:
    def __init__(self, store: Store, room_id: str, on_commit: OnCommit | None = None) -> None:
        self.store = store
        self.id = room_id
        self._doc: Doc = {}
        self._seq = 0
        self._lock = threading.RLock()
        self.lock = self._lock
        self._on_commit = on_commit

    # lifecycle -------------------------------------------------------------------------
    @classmethod
    @registry_operation
    def create(
        cls,
        store: Store,
        room_id: str,
        title: str,
        on_commit: OnCommit | None = None,
        studio: str = "",
    ):
        studio = studio or default_case()
        if studio not in all_extensions("cases"):
            raise NotFound(f"no studio {studio}")
        room = cls(store, room_id, on_commit)
        store.create_room(room_id, title)
        if store.last_seq(room_id) == 0:
            room._commit(
                Actor.backend(),
                "room.created",
                [{"op": "add", "path": "", "value": empty_document(room_id, title, studio)}],
                {"title": title, "studio": studio},
                op_id=f"create:{room_id}",
            )
        else:
            room.reload()
        return room

    @classmethod
    def load(cls, store: Store, room_id: str, on_commit: OnCommit | None = None):
        room = cls(store, room_id, on_commit)
        room.reload()
        return room

    def reload(self) -> None:
        """Rebuild the document from the log alone."""
        with self._lock:
            doc: Doc = {}
            seq = 0
            while True:
                page = self.store.log(self.id, after=seq)
                if not page:
                    break
                for row in page:
                    doc = apply(doc, row.patch)
                    seq = row.seq
            if seq == 0:
                raise NotFound(f"room {self.id} has no log")
            self._doc, self._seq = doc, seq
            for pid, node in cast(
                dict[str, dict[str, Any]], get(doc, "room", "people") or {}
            ).items():
                self.store.add_seat(self.id, pid, str(node.get("role", "")))

    @property
    def seq(self) -> int:
        return self._seq

    def case_definitions(self) -> dict[str, CaseDef]:
        schemas = self._doc.get("schemas")
        if schemas is None:  # Historical documents remain readable but require migration to run.
            return {}
        return {key: CaseDef.model_validate(value) for key, value in schemas.items()}

    def role_descriptors(self) -> dict[str, dict[str, Any]]:
        plugins = cast(list[dict[str, Any]], self.read("room", "plugins") or [])
        roles: dict[str, dict[str, Any]] = {
            role["id"]: role for plugin in plugins for role in plugin["roles"]
        }
        for case in self.case_definitions().values():
            for lane in case.lanes:
                roles.setdefault(
                    lane.default_role, {"id": lane.default_role, "label": lane.default_role}
                )
        return roles

    def default_role_config(self, role: str) -> AgentConfig:
        if role not in self.role_descriptors():
            raise Denied(f"role {role!r} is not supplied by an active room plugin")
        stored = self.read("roleDefaults", role)
        return (
            AgentConfig.model_validate(stored) if stored else AgentConfig(autonomy=Autonomy.SUGGEST)
        )

    @locked
    def activate_plugin(self, pid: str, plugin_id: str) -> None:
        self._require_seat(pid)
        patch = activation_patch(self._doc, plugin_id)
        if patch:
            self._commit(Actor.person(pid), "plugin.activated", patch, {"plugin": plugin_id})

    def snapshot(self) -> Doc:
        with self._lock:
            return snapshot_catalog(self._doc)

    def snapshot_at(self) -> tuple[Doc, int]:
        """The document and the log seq it reflects, taken together."""
        with self._lock:
            return snapshot_catalog(self._doc), self._seq

    def read(self, *path: str) -> Any:
        with self._lock:
            return get(self._doc, *path)

    def _commit(
        self,
        actor: Actor,
        kind: str,
        patch: Patch,
        meta: dict[str, Any],
        op_id: str | None = None,
        lessons: list[tuple[str, str, dict[str, Any]]] | None = None,
    ) -> tuple[LogRow, bool]:
        """Commit, then apply. Returns (row, fresh); a duplicate op_id returns the original."""
        with self._lock:
            if op_id is not None:
                prior = self.existing_op(actor, op_id)
                if prior is not None:
                    if prior.kind != kind:
                        raise Conflict(f"op id {op_id} was already used for {prior.kind}")
                    return prior, False
            candidate = apply(self._doc, patch)
            meta = {**meta, "actor": actor.id, "actorKind": actor.kind}
            if actor.person_id:
                meta.setdefault("person", actor.person_id)
            try:
                row = self.store.append(
                    self.id, op_id or new_id("op"), actor.id, kind, patch, meta, lessons
                )
            except DuplicateOp as dup:
                return dup.row, False
            self._doc, self._seq = candidate, row.seq
        if self._on_commit is not None:
            self._on_commit(row)
        return row, True

    def existing_op(self, actor: Actor, op_id: str) -> LogRow | None:
        """A retried op is recognised before any validation against the changed state.
        An op id belongs to the actor who first used it."""
        prior = self.store.find_op(self.id, op_id)
        if prior is not None and prior.actor != actor.id:
            raise Conflict(f"op id {op_id} was already used by another actor")
        return prior

    def note(
        self, actor: Actor, kind: str, meta: dict[str, Any], op_id: str | None = None
    ) -> LogRow:
        """A log entry that changes nothing (for the decision log)."""
        return self._commit(actor, kind, [], meta, op_id)[0]

    # seats -----------------------------------------------------------------------------
    @locked
    def join(
        self,
        pid: str,
        role: Role,
        op_id: str | None = None,
        config: AgentConfig | None = None,
    ) -> LogRow:
        """Seat a person (up to four). ``config`` replaces the role's default drawer: it is how
        a person's chosen provider, model and autonomy reach their agent."""
        person = self.store.person(pid)
        if person is None:
            raise NotFound(f"no person {pid!r}; register them first")
        roles = self.role_descriptors()
        if role not in roles:
            raise Denied(f"role {role!r} is not supplied by an active room plugin")
        if self.read("room", "people", pid) is not None:
            raise Conflict(f"{pid} already has a seat in this room")
        if len(self.read("room", "people") or {}) >= MAX_SEATS:
            raise RoomFull(f"this room has {MAX_SEATS} seats and they are taken")
        paper, stage = COLORS.get(str(person["color"]), COLORS["blue"])
        aid = agent_id(pid)
        patch: Patch = [
            {
                "op": "add",
                "path": pointer("room", "people", pid),
                "value": {
                    "id": pid,
                    "name": person["name"],
                    "color": paper,
                    "stageColor": stage,
                    "colorName": person["color"],
                    "role": role,
                    "roleLabel": roles[role]["label"],
                },
            },
            {
                "op": "add",
                "path": pointer("room", "agents", aid),
                "value": {
                    "id": aid,
                    "kind": "personal",
                    "owner": pid,
                    "status": "idle",
                    "statusDetail": "",
                    "brought": False,
                    "cardUrl": None,
                    "config": (config or self.default_role_config(role)).wire(),
                },
            },
        ]
        row = self._commit(Actor.person(pid), "room.join", patch, {"role": role}, op_id)[0]
        self.store.add_seat(self.id, pid, role)
        return row

    def _require_seat(self, pid: str) -> None:
        if self.read("room", "people", pid) is None:
            raise Denied(f"{pid} has no seat in this room")

    # lanes -----------------------------------------------------------------------------
    @locked
    def claim_lane(self, pid: str, case: str, lane: str, op_id: str | None = None) -> LogRow:
        self._require_seat(pid)
        self._lane_def(case, lane)
        owner = self.read("lanes", case, lane)
        if owner == pid:
            return self.note(Actor.person(pid), "lane.claim.noop", {"case": case, "lane": lane})
        if owner is not None:
            raise Conflict(f"lane {case}/{lane} is owned by {owner}")
        patch: Patch = [{"op": "replace", "path": pointer("lanes", case, lane), "value": pid}]
        patch += self._transfer_pending(case, lane, pid)
        meta = {"case": case, "lane": lane}
        return self._commit(Actor.person(pid), "lane.claim", patch, meta, op_id)[0]

    @locked
    def release_lane(self, pid: str, case: str, lane: str, op_id: str | None = None) -> LogRow:
        self._lane_def(case, lane)
        if self.read("lanes", case, lane) != pid:
            raise Denied(f"only the owner releases lane {case}/{lane}")
        patch: Patch = [{"op": "replace", "path": pointer("lanes", case, lane), "value": None}]
        patch += self._transfer_pending(case, lane, None)
        meta = {"case": case, "lane": lane}
        return self._commit(Actor.person(pid), "lane.release", patch, meta, op_id)[0]

    def take_role_lanes(self, pid: str) -> LogRow | None:
        """On joining, a person takes the lanes their role owns by default, in every studio:
        the open ones, and those held by someone of the other role (a colleague who took every
        lane while working alone). A lane held by someone of the same role stays theirs. This
        keeps one owner per lane and brings the cross-check back as soon as the team forms."""
        self._require_seat(pid)
        role = str(self.read("room", "people", pid, "role") or "")
        people = cast(dict[str, dict[str, Any]], self.read("room", "people") or {})
        patch: Patch = []
        taken: list[str] = []
        handed: dict[str, str] = {}
        for case, lanes in self.lane_owners().items():
            cdef = self.case_definitions().get(case)
            if cdef is None:
                continue
            for ldef in cdef.lanes:
                if ldef.default_role != role:
                    continue
                owner = lanes.get(ldef.id)
                if owner == pid:
                    continue
                if owner is not None and str(people.get(owner, {}).get("role")) == role:
                    continue  # a colleague in the same role keeps it
                patch.append(
                    {"op": "replace", "path": pointer("lanes", case, ldef.id), "value": pid}
                )
                patch += self._transfer_pending(case, ldef.id, pid)
                key = f"{case}/{ldef.id}"
                taken.append(key)
                if owner is not None:
                    handed[key] = str(owner)
        if not patch:
            return None
        meta = {"lanes": taken, "from": handed}
        kind = "lane.handover" if handed else "lane.claim"
        return self._commit(Actor.person(pid), kind, patch, meta)[0]

    def take_every_lane(self, pid: str, case: str) -> LogRow | None:
        """Working alone: one person takes every open lane of a studio. Colleagues who join
        later get their role's lanes handed over (``take_role_lanes``)."""
        self._require_seat(pid)
        patch: Patch = []
        taken: list[str] = []
        for lane, owner in (self.lane_owners().get(case) or {}).items():
            if owner is None:
                patch.append({"op": "replace", "path": pointer("lanes", case, lane), "value": pid})
                patch += self._transfer_pending(case, lane, pid)
                taken.append(f"{case}/{lane}")
        if not patch:
            return None
        return self._commit(Actor.person(pid), "lane.claim.all", patch, {"lanes": taken})[0]

    def leave(self, pid: str) -> LogRow:
        """A person gives up their seat: their lanes open again (pending approvals with them),
        and their agent leaves with them. The room's history keeps what they did."""
        self._require_seat(pid)
        patch: Patch = []
        for case, lanes in self.lane_owners().items():
            for lane, owner in lanes.items():
                if owner == pid:
                    patch.append(
                        {"op": "replace", "path": pointer("lanes", case, lane), "value": None}
                    )
                    patch += self._transfer_pending(case, lane, None)
        aid = agent_id(pid)
        if self.read("room", "agents", aid) is not None:
            patch.append({"op": "remove", "path": pointer("room", "agents", aid)})
        patch.append({"op": "remove", "path": pointer("room", "people", pid)})
        return self._commit(Actor.person(pid), "room.leave", patch, {"person": pid})[0]

    def _transfer_pending(self, case: str, lane: str, owner: str | None) -> Patch:
        """A pending approval belongs to whoever owns the lane now, never a former owner."""
        ops: Patch = []
        proposals = cast(dict[str, dict[str, Any]], self.read("proposals") or {})
        for prop_id, node in proposals.items():
            if (
                node.get("case") == case
                and node.get("targetLane") == lane
                and node.get("status") == "awaiting-owner"
            ):
                path = pointer("proposals", prop_id, "laneOwner")
                ops.append({"op": "add", "path": path, "value": owner})  # set or overwrite
        return ops

    def lane_owners(self) -> dict[str, dict[str, Any]]:
        return cast(dict[str, dict[str, Any]], self.read("lanes") or {})

    def case_ready(self, case: str) -> bool:
        owners = self.lane_owners().get(case, {})
        return bool(owners) and all(v is not None for v in owners.values())

    def require_ready(self, case: str) -> None:
        if not self.case_ready(case):
            open_lanes = [k for k, v in self.lane_owners().get(case, {}).items() if v is None]
            raise Locked(f"case {case} is locked until every lane is owned", open=open_lanes)

    def _lane_def(self, case: str, lane: str) -> None:
        cdef = self.case_definitions().get(case)
        if cdef is None or cdef.lane(lane) is None:
            raise NotFound(f"no lane {case}/{lane}")

    # agents ----------------------------------------------------------------------------
    def agent(self, aid: str) -> dict[str, Any]:
        node = self.read("room", "agents", aid)
        if node is None:
            raise NotFound(f"no agent {aid}")
        return cast(dict[str, Any], node)

    def agent_config(self, aid: str) -> AgentConfig:
        return AgentConfig.model_validate(self.agent(aid)["config"])

    @locked
    def set_agent_config(
        self, pid: str, aid: str, config: AgentConfig, op_id: str | None = None
    ) -> LogRow:
        """The drawer. Only the agent's owner changes it, and the change is logged (FR-AG-1)."""
        if self.agent(aid).get("owner") != pid:
            raise Denied("only an agent's owner configures it")
        patch: Patch = [
            {
                "op": "replace",
                "path": pointer("room", "agents", aid, "config"),
                "value": config.wire(),
            }
        ]
        return self._commit(Actor.person(pid), "agent.config", patch, {"agent": aid}, op_id)[0]

    def _status_ops(self, aid: str, status: str, detail: str = "") -> Patch:
        """The ops that set an agent's visible status (FR-AG-3); none when it is unchanged."""
        if status not in AGENT_STATUSES:
            raise Denied(f"unknown agent status {status!r}")
        node = self.agent(aid)
        if node.get("status") == status and node.get("statusDetail") == detail:
            return []
        return [
            {"op": "replace", "path": pointer("room", "agents", aid, "status"), "value": status},
            {
                "op": "replace",
                "path": pointer("room", "agents", aid, "statusDetail"),
                "value": detail,
            },
        ]

    @locked
    def set_agent_status(self, aid: str, status: str, detail: str = "") -> LogRow | None:
        patch = self._status_ops(aid, status, detail)
        if not patch:
            return None
        meta = {"agent": aid, "status": status, "detail": detail}
        return self._commit(Actor.backend(), "agent.status", patch, meta)[0]

    @locked
    def set_brought_agent(self, pid: str, card_url: str, card: dict[str, Any]) -> LogRow:
        """Replace a person's spawned agent with one they brought (it keeps their permissions)."""
        aid = agent_id(pid)
        self.agent(aid)
        if self.agent(aid).get("owner") != pid:
            raise Denied("only the owner replaces their agent")
        patch: Patch = [
            {"op": "replace", "path": pointer("room", "agents", aid, "brought"), "value": True},
            {"op": "replace", "path": pointer("room", "agents", aid, "cardUrl"), "value": card_url},
            {"op": "add", "path": pointer("room", "agents", aid, "card"), "value": card},
        ]
        return self._commit(Actor.person(pid), "agent.brought", patch, {"cardUrl": card_url})[0]

    def actor_config(self, actor: Actor) -> AgentConfig | None:
        return self.agent_config(actor.id) if actor.kind == "personal_agent" else None

    # twin ------------------------------------------------------------------------------
    def edit_twin(
        self,
        actor: Actor,
        patch: list[PatchOp],
        reason: str,
        op_id: str | None = None,
    ) -> LogRow:
        """A direct edit. Raises LaneNotOwned when the author must propose instead."""
        with self._lock:
            if op_id is not None:
                prior = self.existing_op(actor, op_id)
                if prior is not None:
                    return prior
            gate = authorize_twin_patch(
                actor, patch, self.lane_owners(), self.actor_config(actor), self.case_definitions()
            )
            if gate.outcome == "lane_not_owned":
                raise LaneNotOwned(gate.reason, [lp.key for lp in gate.blocked], gate.owners)
            if gate.outcome == "propose_to_owner":
                raise LaneNotOwned(
                    f"{actor.id} is in suggest mode: it proposes to its owner",
                    [lp.key for lp in gate.lanes],
                    gate.owners,
                )
            wire = [op.wire() for op in patch]
            full = wire + self._twin_side_effects(wire, gate.lanes)
            meta = {"reason": reason, "lanes": [lp.key for lp in gate.lanes]}
            return self._commit(actor, "twin.edit", full, meta, op_id)[0]

    def _twin_side_effects(self, wire: Patch, lanes: list[LanePath]) -> Patch:
        """Validate the candidate lanes, then add revision updates and mark results stale."""
        candidate = apply(self._doc, wire)
        for lp in lanes:
            try:
                validate_lane(
                    self.case_definitions()[lp.case],
                    lp.lane,
                    get(candidate, "twin", lp.case, lp.lane) or {},
                )
            except SpecError as exc:
                raise SpecInvalid(exc.field, exc.message) from exc
        extra: Patch = []
        changed: dict[str, str] = {}
        for lp in lanes:
            rev = lane_revision(candidate, lp.case, lp.lane)
            if rev != self.read("revisions", lp.case, lp.lane):
                changed[lp.key] = rev
                extra.append(
                    {"op": "replace", "path": pointer("revisions", lp.case, lp.lane), "value": rev}
                )
        if changed:
            extra += self._stale_ops(changed)
        return extra

    def _stale_ops(self, changed: dict[str, str]) -> Patch:
        ops: Patch = []
        results = cast(dict[str, dict[str, Any]], self.read("results") or {})
        for rid, res in results.items():
            if res.get("status") != "current":
                continue
            revs = cast(dict[str, str], res.get("inputRevisions") or {})
            if any(k in revs and revs[k] != v for k, v in changed.items()):
                ops.append(
                    {"op": "replace", "path": pointer("results", rid, "status"), "value": "stale"}
                )
                ops.append(
                    {"op": "replace", "path": pointer("results", rid, "stamp"), "value": "warning"}
                )
        return ops

    def revisions(self, case: str) -> dict[str, str]:
        revs = cast(dict[str, str], self.read("revisions", case) or {})
        return {f"{case}/{lane}": rev for lane, rev in revs.items()}

    @locked
    def set_active_case(self, pid: str, case: str) -> LogRow:
        self._require_seat(pid)
        if case not in self.case_definitions():
            raise NotFound(f"no case {case}")
        patch: Patch = [{"op": "replace", "path": "/room/activeCase", "value": case}]
        return self._commit(Actor.person(pid), "room.case", patch, {"case": case})[0]
