"""The room aggregate, part two: proposals, decisions, warnings, memory, runs, results, ledger.

Every mutation holds the room lock from its first read to its commit (``@locked``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Literal, cast

from consilith_contracts import (
    AdHocPlan,
    AgentConfig,
    Autonomy,
    ChatTurn,
    Decision,
    LedgerEntry,
    Lesson,
    MemoryScope,
    PatchOp,
    PendingActionKind,
    Proposal,
    ProposalStatus,
    ResultRecord,
    ResultStatus,
    Stamp,
    Tier,
    TurnRole,
    WarningFlag,
    new_id,
    pending_action_digest,
)
from consilith_contracts.results import ArtifactRef, OutputKind, result_binding_digest
from pydantic import JsonValue

from ..platform.store import LogRow
from .actors import Actor, agent_id
from .document import Patch
from .errors import Conflict, Denied, NotFound, RoomError
from .gate import authorize_twin_patch
from .lessons import rule_from_reason
from .paths import pointer, twin_lane
from .peers import PeerRoom
from .plugin_access import room_pins
from .room import locked

Verifier = Callable[[LedgerEntry], bool]
RUN_ACTIVE = "running"
# How long a minted run grant stays usable. A run starts right after the person approves; a
# queued job gets a fresh grant when the worker takes it, after its digest is checked again.
GRANT_TTL_S = 15 * 60
LESSON_ROOM_KEY = "room"  # the room a stored personal lesson was taught in (absent: legacy)


def run_card_args(case: str, mode: str) -> dict[str, str]:
    """The resolved arguments of a ``run.start`` card. They are the same for the card and for
    the recomputation that checks a grant, so the digest can be rebuilt from the run alone."""
    return {"capability": case, "case": case, "mode": mode}


def split_lesson(raw: dict[str, Any]) -> tuple[Lesson, str | None]:
    """A stored personal lesson and the room it was taught in (None for a legacy one)."""
    body = {k: v for k, v in raw.items() if k != LESSON_ROOM_KEY}
    tag = raw.get(LESSON_ROOM_KEY)
    return Lesson.model_validate(body), tag if isinstance(tag, str) and tag else None


MAX_PINS_PER_PERSON = 12
MAX_PINS_PER_ROOM = 40
MAX_SHARE_RECIPIENTS = 3  # a room seats four, and the owner is one of them
_PIN_HANDLE_KINDS = frozenset({"result", "input", "artifact", "run", "proposal"})
_PIN_HANDLE_FIELDS = ("kind", "id", "label", "unit", "case", "lane", "output")


def result_digest(
    quantity: str,
    value: JsonValue,
    unit: str,
    run_id: str,
    manifest: str,
    artifacts: list[ArtifactRef] | None = None,
    *,
    kind: OutputKind = "number",
) -> str:
    return result_binding_digest(quantity, value, unit, run_id, manifest, artifacts, kind=kind)


class RoomService(PeerRoom):
    # proposals (FR-MP-3, FR-LOOP-7) ----------------------------------------------------
    @locked
    def propose(
        self,
        actor: Actor,
        case: str,
        target_lane: str,
        patch: list[PatchOp],
        reason: str,
        evidence: list[str] | None = None,
        op_id: str | None = None,
    ) -> Proposal:
        if actor.kind != "personal_agent" or actor.owner is None:
            raise Denied("only a personal agent proposes")
        if op_id is not None:
            prior = self.existing_op(actor, op_id)
            if prior is not None:
                return self.proposal(cast(str, prior.meta["proposal"]))
        cfg = self.agent_config(actor.id)
        if cfg.autonomy == Autonomy.OBSERVE:
            raise Denied(f"{actor.id} is set to observe: it may not propose")
        gate = authorize_twin_patch(
            Actor.person(actor.owner), patch, self.lane_owners(), None, self.case_definitions()
        )
        keys = {lp.key for lp in gate.lanes}
        if keys != {f"{case}/{target_lane}"}:
            raise Denied("a proposal targets exactly one lane; split it")
        owner = self.lane_owners()[case][target_lane]
        # Validate the candidate spec now, so the owner never sees an impossible change.
        self._twin_side_effects([op.wire() for op in patch], gate.lanes)
        prop = Proposal(
            id=new_id("prop"),
            room=self.id,
            author=actor.id,
            on_behalf_of=actor.owner,
            case=case,
            target_lane=target_lane,
            lane_owner=owner,
            patch=patch,
            reason=reason,
            evidence=evidence or [],
            interrupt_id=new_id("int"),
            created_seq=self.seq + 1,
        )
        ops: Patch = [{"op": "add", "path": pointer("proposals", prop.id), "value": prop.wire()}]
        # The proposer pauses until the owner answers (FR-LOOP-7), in the same commit.
        ops += self._status_ops(actor.id, "paused", f"approval from {owner or 'the lane owner'}")
        meta = {"proposal": prop.id, "lane": f"{case}/{target_lane}", "owner": owner}
        self._commit(actor, "proposal.open", ops, meta, op_id)
        return prop

    def proposal(self, pid: str) -> Proposal:
        node = self.read("proposals", pid)
        if node is None:
            raise NotFound(f"no proposal {pid}")
        return Proposal.model_validate(node)

    def settled_status(self, aid: str, ignore: str | None = None) -> tuple[str, str]:
        """An agent at rest: paused while a proposal of its own awaits an owner, else idle."""
        for prop in self.open_proposals():
            if prop.author == aid and prop.id != ignore:
                return "paused", f"approval from {prop.lane_owner or 'the lane owner'}"
        return "idle", ""

    @locked
    def settle_agent_status(self, aid: str) -> LogRow | None:
        return self.set_agent_status(aid, *self.settled_status(aid))

    def open_proposals(self, owner: str | None = None) -> list[Proposal]:
        out: list[Proposal] = []
        for node in cast(dict[str, Any], self.read("proposals") or {}).values():
            prop = Proposal.model_validate(node)
            if prop.status == ProposalStatus.AWAITING_OWNER and (
                owner is None or prop.lane_owner == owner
            ):
                out.append(prop)
        return sorted(out, key=lambda p: p.created_seq)

    @locked
    def set_proposal_task(self, prop_id: str, task_id: str) -> None:
        self.proposal(prop_id)
        path = pointer("proposals", prop_id, "a2aTask")
        ops: Patch = [{"op": "add", "path": path, "value": task_id}]  # set or overwrite
        self._commit(Actor.backend(), "proposal.task", ops, {"proposal": prop_id, "task": task_id})

    def decide(
        self,
        person: str,
        prop_id: str,
        accept: bool,
        reason: str,
        op_id: str | None = None,
        lesson_rule: dict[str, Any] | None = None,
    ) -> Decision:
        return self.decide_once(person, prop_id, accept, reason, op_id, lesson_rule)[0]

    @locked
    def decide_once(
        self,
        person: str,
        prop_id: str,
        accept: bool,
        reason: str,
        op_id: str | None = None,
        lesson_rule: dict[str, Any] | None = None,
    ) -> tuple[Decision, bool]:
        """Only the lane's current owner decides, always with a reason (FR-MP-5), exactly
        once. Returns (decision, fresh); follow-up work runs only for a fresh decision.
        The decision and the lesson it teaches commit in one transaction (FR-MEM-1)."""
        reason = reason.strip()
        if not reason:
            raise RoomError("a decision needs a one-line reason")
        prop = self.proposal(prop_id)
        existing = self._decision_for(prop_id)
        if existing is not None:
            if existing.by != person:  # a retry is authorised like the original
                raise Denied(f"only {existing.by}, who decided {prop_id}, may retry it")
            return existing, False
        if prop.status != ProposalStatus.AWAITING_OWNER:
            raise Conflict(f"proposal {prop_id} is {prop.status}")
        current_owner = self.lane_owners().get(prop.case, {}).get(prop.target_lane)
        if prop.lane_owner != person or current_owner != person:
            raise Denied(f"only the current owner of {prop.case}/{prop.target_lane} decides")
        decision = Decision(
            id=new_id("dec"),
            room=self.id,
            proposal=prop_id,
            by=person,
            accept=accept,
            reason=reason,
        )
        status = ProposalStatus.ACCEPTED if accept else ProposalStatus.DECLINED
        ops: Patch = []
        failure: str | None = None
        if accept:
            try:
                gate = authorize_twin_patch(
                    Actor.person(person),
                    prop.patch,
                    self.lane_owners(),
                    None,
                    self.case_definitions(),
                )
                if gate.outcome != "allow":
                    raise Denied("the patch no longer falls inside the owner's lanes")
                wire = [op.wire() for op in prop.patch]
                lanes = list(
                    dict.fromkeys(twin_lane(op.path, self.case_definitions()) for op in prop.patch)
                )
                ops = wire + self._twin_side_effects(wire, lanes)
            except RoomError as exc:
                failure, ops, status = exc.message, [], ProposalStatus.FAILED
        if lesson_rule is None and not accept:
            last = twin_lane(prop.patch[0].path, self.case_definitions()).rest
            lesson_rule = rule_from_reason(reason, last[0]) if last else None
        # A proposal a peer message caused teaches its author nothing unless the author's owner
        # opted in (decision 20): the decider's reason text must not reach that agent's memory.
        teaches = self.peer_lesson_allowed(prop_id, prop.on_behalf_of)
        # Approving your own agent's proposal teaches it nothing (a one-tap confirm).
        lesson = (
            None
            if not teaches or (accept and prop.on_behalf_of == person)
            else self._lesson(prop, decision, lesson_rule)
        )
        ops += [
            {
                "op": "replace",
                "path": pointer("proposals", prop_id, "status"),
                "value": status.value,
            },
            {"op": "add", "path": pointer("decisions", decision.id), "value": decision.wire()},
        ]
        ops += self._status_ops(prop.author, *self.settled_status(prop.author, ignore=prop_id))
        if self.read("followups") is None:
            ops.append({"op": "add", "path": "/followups", "value": {}})
        followup = {
            "decision": decision.id,
            "proposal": prop_id,
            "accept": accept,
            "status": "pending",
        }
        ops.append({"op": "add", "path": pointer("followups", decision.id), "value": followup})
        meta: dict[str, Any] = {
            "proposal": prop_id,
            "decision": decision.id,
            "accept": accept,
            "reason": reason,
            "lane": f"{prop.case}/{prop.target_lane}",
            "evidence": prop.evidence,
        }
        if lesson is not None:
            meta["lesson"] = {"id": lesson.id, "holder": lesson.holder, "text": lesson.text}
        if failure:
            meta["failure"] = failure
        lessons = (
            None
            if lesson is None
            else [(prop.on_behalf_of, lesson.id, {**lesson.wire(), LESSON_ROOM_KEY: self.id})]
        )
        row, fresh = self._commit(
            Actor.person(person),
            "proposal.decide",
            ops,
            meta,
            op_id or f"decide:{prop_id}",
            lessons,
        )
        if not fresh:
            if row.meta.get("proposal") != prop_id:
                raise Conflict(f"op id was already used to decide {row.meta.get('proposal')}")
            prior = self._decision_for(prop_id)
            if prior is None:
                raise Conflict("the earlier decision for this op id is missing")
            return prior, False
        return decision.model_copy(update={"seq": row.seq}), True

    def pending_followup(self, prop_id: str) -> str | None:
        """The decision id whose follow-up work has not completed, if any."""
        for dec_id, node in cast(dict[str, Any], self.read("followups") or {}).items():
            if node.get("proposal") == prop_id and node.get("status") == "pending":
                return dec_id
        return None

    @locked
    def complete_followup(self, dec_id: str) -> None:
        node = self.read("followups", dec_id)
        if node is None or node.get("status") != "pending":
            return
        path = pointer("followups", dec_id, "status")
        ops: Patch = [{"op": "replace", "path": path, "value": "done"}]
        self._commit(Actor.backend(), "followup.done", ops, {"decision": dec_id})

    @locked
    def decide_batch(
        self, person: str, items: list[tuple[str, bool, str, str | None]]
    ) -> list[tuple[Decision, bool]]:
        """Decide several proposals for one person. Every item is checked first, under the
        lock; if any is not this person's to decide, nothing is decided."""
        for prop_id, _accept, reason, _op in items:
            if not reason.strip():
                raise RoomError("a decision needs a one-line reason")
            prop = self.proposal(prop_id)
            existing = self._decision_for(prop_id)
            if existing is not None:
                if existing.by != person:
                    raise Denied(f"only {existing.by}, who decided {prop_id}, may retry it")
                continue
            owner = self.lane_owners().get(prop.case, {}).get(prop.target_lane)
            if prop.status != ProposalStatus.AWAITING_OWNER:
                raise Conflict(f"proposal {prop_id} is {prop.status}")
            if prop.lane_owner != person or owner != person:
                raise Denied(f"only the current owner of {prop.case}/{prop.target_lane} decides")
        return [self.decide_once(person, pid, acc, rsn, op) for pid, acc, rsn, op in items]

    def _decision_for(self, prop_id: str) -> Decision | None:
        for node in cast(dict[str, Any], self.read("decisions") or {}).values():
            if node.get("proposal") == prop_id:
                return Decision.model_validate(node)
        return None

    def _lesson(self, prop: Proposal, decision: Decision, rule: dict[str, Any] | None) -> Lesson:
        verb = "accepted" if decision.accept else "declined"
        return Lesson(
            id=f"les-{decision.id}",
            scope=MemoryScope.PERSONAL,
            holder=prop.author,
            text=f"{decision.by} {verb}: {decision.reason}",
            taught_by=decision.by,
            reason=decision.reason,
            source_decision=decision.id,
            ts=time.time(),
            rule=rule,
        )

    # warnings (FR-MP-6) -----------------------------------------------------------------
    @locked
    def warn(
        self,
        actor: Actor,
        to: str,
        case: str,
        lane: str,
        message: str,
        evidence: list[str] | None = None,
    ) -> WarningFlag:
        if actor.kind not in ("personal_agent", "room_agent"):
            raise Denied("only agents raise warnings")
        self._lane_def(case, lane)
        w = WarningFlag(
            id=new_id("warn"),
            room=self.id,
            author=actor.id,
            to=to,
            case=case,
            lane=lane,
            message=message,
            evidence=evidence or [],
        )
        ops: Patch = [{"op": "add", "path": pointer("warnings", w.id), "value": w.wire()}]
        self._commit(actor, "warning.open", ops, {"warning": w.id, "to": to, "message": message})
        return w

    @locked
    def acknowledge(self, person: str, warning_id: str) -> None:
        node = self.read("warnings", warning_id)
        if node is None:
            raise NotFound(f"no warning {warning_id}")
        if node["to"] != person:
            raise Denied("only the person a warning is addressed to acknowledges it")
        path = pointer("warnings", warning_id, "acknowledged")
        ops: Patch = [{"op": "replace", "path": path, "value": True}]
        self._commit(Actor.person(person), "warning.ack", ops, {"warning": warning_id})

    # memory (FR-MEM-*) -----------------------------------------------------------------
    def personal_lessons(self, person: str) -> list[Lesson]:
        """Every personal lesson of this person, from any room (their own memory page)."""
        out: list[Lesson] = []
        for raw in self.store.lessons(person):
            les, _tag = split_lesson(raw)
            if not les.deleted:
                out.append(les)
        return out

    @locked
    def edit_personal_lesson(
        self, person: str, lesson_id: str, *, delete: bool, text: str | None = None
    ) -> None:
        for raw in self.store.lessons(person):
            if raw["id"] == lesson_id:
                les, tag = split_lesson(raw)
                les = les.model_copy(update={"deleted": delete, "text": text or les.text})
                body = {**les.wire(), **({LESSON_ROOM_KEY: tag} if tag else {})}
                self.store.put_lesson(person, lesson_id, body)
                kind = "memory.delete" if delete else "memory.edit"
                self.note(Actor.person(person), kind, {"scope": "personal", "lesson": lesson_id})
                return
        raise NotFound(f"no lesson {lesson_id} for {person}")

    def _lesson_actor(self, person: str, actor: Actor | None) -> tuple[Actor, dict[str, Any]]:
        """Who commits a lesson, and the meta that records both parties: the person alone, or an
        agent that asked with the person who approved."""
        if actor is None or actor.kind == "person":
            return Actor.person(person), {}
        return actor, {"requestedBy": actor.id, "approvedBy": person}

    @locked
    def add_shared_lesson(
        self,
        person: str,
        scope: MemoryScope,
        text: str,
        case: str | None = None,
        lane: str | None = None,
        rule: dict[str, Any] | None = None,
        *,
        actor: Actor | None = None,
        op_id: str | None = None,
    ) -> Lesson:
        """A room or case lesson. ``actor`` is the agent that asked, when a confirm card made
        the person approve it: the row then records ``requestedBy`` and ``approvedBy``."""
        self._require_seat(person)
        who, extra = self._lesson_actor(person, actor)
        if op_id is not None:
            prior = self.existing_op(who, op_id)
            if prior is not None:
                found = self._shared_lesson(str(prior.meta.get("lesson", "")))
                if found is not None:
                    return found
        if scope == MemoryScope.CASE:
            owner = self.lane_owners().get(case or "", {}).get(lane or "")
            if case is None or lane is None or owner != person:
                raise Denied("only a lane owner adds a case lesson for that lane")
            holder, path = f"{self.id}:{case}", ("memory", "case")
        elif scope == MemoryScope.ROOM:
            holder, path = self.id, ("memory", "room")
        else:
            raise Denied("personal lessons come from decisions")
        les = Lesson(
            id=new_id("les"),
            scope=scope,
            holder=holder,
            text=text,
            taught_by=person,
            reason=text,
            ts=time.time(),
            rule=rule,
        )
        ops: Patch = [{"op": "add", "path": pointer(*path, les.id), "value": les.wire()}]
        meta = {"scope": scope.value, "lesson": les.id, **extra}
        self._commit(who, "memory.add", ops, meta, op_id)
        return les

    def _shared_lesson(self, lesson_id: str) -> Lesson | None:
        for scope in ("room", "case"):
            raw = cast(dict[str, Any], self.read("memory", scope) or {}).get(lesson_id)
            if raw is not None:
                return Lesson.model_validate(raw)
        return None

    @locked
    def add_personal_note(
        self,
        person: str,
        text: str,
        *,
        actor: Actor | None = None,
        op_id: str | None = None,
    ) -> Lesson:
        """A note the person keeps for their own agent in this room (class A: they wrote or
        approved it). It is stored with its room, so no other room's agent reads it."""
        self._require_seat(person)
        who, extra = self._lesson_actor(person, actor)
        if op_id is not None:
            prior = self.existing_op(who, op_id)
            if prior is not None:
                for les in self.personal_lessons(person):
                    if les.id == prior.meta.get("lesson"):
                        return les
        les = Lesson(
            id=new_id("les"),
            scope=MemoryScope.PERSONAL,
            holder=agent_id(person),
            text=text,
            taught_by=person,
            reason=text,
            ts=time.time(),
        )
        self.store.put_lesson(person, les.id, {**les.wire(), LESSON_ROOM_KEY: self.id})
        self._commit(who, "memory.add", [], {"scope": "personal", "lesson": les.id, **extra}, op_id)
        return les

    def lessons_with_origin(
        self, aid: str, scope: Literal["room", "all"] = "room"
    ) -> list[tuple[Lesson, str | None]]:
        """What an agent consults, with the room each personal lesson was taught in. ``room``
        (the default) is this room's personal lessons plus its room and case lessons. ``all``
        adds the personal lessons of other rooms and the legacy untagged ones (origin None)."""
        node = self.agent(aid)
        out: list[tuple[Lesson, str | None]] = []
        if node.get("owner"):
            for raw in self.store.lessons(cast(str, node["owner"])):
                les, tag = split_lesson(raw)
                if les.deleted or les.holder != aid:
                    continue
                if tag == self.id or scope == "all":
                    out.append((les, tag))
        for kind in ("room", "case"):
            for raw in cast(dict[str, Any], self.read("memory", kind) or {}).values():
                les = Lesson.model_validate(raw)
                if not les.deleted:
                    out.append((les, self.id))
        return out

    def chat_rows(
        self,
        person: str,
        roles: tuple[TurnRole, ...],
        limit: int,
        *,
        before_seq: int | None = None,
    ) -> list[ChatTurn]:
        """The person's latest ``limit`` chat rows of the given roles, oldest first. Tool rows
        sit between user rows, so a plain "last N rows" read can lose the turns that matter;
        this reads further back until it has enough of the wanted roles or runs out of rows."""
        fetch = max(limit * 4, 40)
        while True:
            rows = self.store.chat_turns.last(self.id, person, fetch)
            picked = [
                t for t in rows if t.role in roles and (before_seq is None or t.seq < before_seq)
            ]
            if len(picked) >= limit or len(rows) < fetch:
                return picked[-limit:] if limit > 0 else []
            fetch *= 4

    def lessons_for(self, aid: str, scope: Literal["room", "all"] = "room") -> list[Lesson]:
        """What an agent consults before proposing: this room's personal lessons plus room and
        case. Lessons taught in another room, and legacy untagged ones, come only with
        ``scope="all"`` (``lessons.recall``), which marks them."""
        return [les for les, _tag in self.lessons_with_origin(aid, scope)]

    # runs and results (FR-LOOP-5, NFR-6, revisions) ------------------------------------
    @locked
    def open_run(
        self,
        actor: Actor,
        case: str,
        tool: str,
        tier: int,
        manifest_digest: str,
        requester: Actor,
        estimate_s: float = 0.0,
        run_id: str | None = None,
        input_revisions: dict[str, str] | None = None,
        job_id: str | None = None,
    ) -> str:
        """Admit a solve. The requesting agent's drawer is enforced here, for hosted and
        brought agents alike: autonomy, the tool allowlist and the compute budget. A valid run
        grant (a card the person approved, still matching the inputs) skips only the autonomy
        test. ``job_id`` joins the run to the job that started it."""
        if actor.id != "conductor":
            raise Denied("only the conductor runs solvers")
        self.require_ready(case)
        granted = False
        if requester.kind == "personal_agent":
            cfg: AgentConfig = self.agent_config(requester.id)
            granted = self.run_grant_valid(requester, case)
            if cfg.autonomy != Autonomy.RUN_SOLVERS and not granted:
                raise Denied(
                    f"{requester.id} may not run solvers (autonomy {cfg.autonomy.value})"
                    + (
                        "; the approval no longer matches the inputs"
                        if requester.grant is not None
                        else ""
                    )
                )
            if not cfg.tool_allowed(tool):
                raise Denied(f"{requester.id} may not call {tool}")
            if tier < 2 and estimate_s > cfg.compute_budget_s:
                raise Denied(
                    f"{tool} needs about {estimate_s:.0f} s, over {requester.id}'s budget of "
                    f"{cfg.compute_budget_s:.0f} s: queue it as tier 2",
                    route="tier2",
                )
        elif requester.kind == "room_agent" and requester.id == "conductor":
            # The conductor's own dependency runs (material data, tier-0 ratios) for a solve
            # it is running for someone; that person or agent was checked on the main run.
            if not requester.initiator:
                raise Denied("a dependency run names the person it serves")
        elif requester.kind != "person":
            raise Denied("a solve is requested by a person or their agent")
        rid = run_id or new_id("run")
        if self.read("runs", rid) is not None:
            raise Conflict(f"run {rid} already exists; run ids are never reused")
        run = {
            "id": rid,
            "case": case,
            "tool": tool,
            "tier": tier,
            "status": RUN_ACTIVE,
            # The revisions the inputs were read at. If the twin moved on since, the run is
            # already stale and its results will be kept as history only.
            "inputRevisions": input_revisions
            if input_revisions is not None
            else self.revisions(case),
            "manifestDigest": manifest_digest,
            "requestedBy": requester.id,
            "initiator": requester.person_id,
            "openedSeq": self.seq + 1,
            "started": time.time(),
        }
        meta: dict[str, Any] = {"run": rid, "tool": tool, "tier": tier}
        if job_id is not None:
            run["jobId"] = job_id
            meta["job"] = job_id
        if requester.kind == "personal_agent":
            meta["requestedBy"] = requester.id
        if granted and requester.grant is not None:
            run["approvedBy"] = requester.grant.approved_by
            run["approvalDigest"] = requester.grant.digest
            meta["approvedBy"] = requester.grant.approved_by
        ops: Patch = [{"op": "add", "path": pointer("runs", rid), "value": run}]
        self._commit(actor, "run.open", ops, meta)
        return rid

    def run_card_digest(
        self, person: str, case: str, mode: str, revisions: dict[str, str] | None = None
    ) -> str:
        """The digest of a ``run.start`` card for this person, case and mode over the lane
        revisions and plugin pins as they are now (or the given revisions)."""
        return pending_action_digest(
            kind=PendingActionKind.CONFIRM_RUN,
            room=self.id,
            person=person,
            ref="run.start",
            mode=mode,
            resolved_args=run_card_args(case, mode),
            input_revisions=self.revisions(case) if revisions is None else revisions,
            plugin_pins=room_pins(self),
        )

    def run_grant_valid(self, requester: Actor, case: str, now: float | None = None) -> bool:
        """Whether the requester carries a run grant for this case that is still good: minted
        for the same person, not expired, and its digest matches a recomputation over the
        current lane revisions and plugin pins. Nothing but a confirm card mints one."""
        grant = requester.grant
        if grant is None or grant.kind != "run" or grant.case != case:
            return False
        if grant.approved_by != requester.person_id:
            return False
        if grant.expired(time.time() if now is None else now):
            return False
        return grant.digest == self.run_card_digest(grant.approved_by, case, grant.mode)

    def run(self, rid: str) -> dict[str, Any]:
        node = self.read("runs", rid)
        if node is None:
            raise NotFound(f"no run {rid}")
        return cast(dict[str, Any], node)

    def run_is_current(self, rid: str) -> bool:
        run = self.run(rid)
        return run["inputRevisions"] == self.revisions(run["case"])

    def _outranks(self, old: dict[str, Any], tier: int, opened_seq: int) -> bool:
        """Whether an existing result beats an incoming one: higher tier, then newer run."""
        old_tier = int(old.get("tier", 1))
        if old_tier != tier:
            return old_tier > tier
        old_run = self.read("runs", str(old.get("runId")))
        old_seq = int(old_run.get("openedSeq", 0)) if old_run else 0
        return old_seq > opened_seq

    @locked
    def record_results(
        self,
        actor: Actor,
        rid: str,
        records: list[ResultRecord],
        verified: tuple[LedgerEntry, Verifier] | None = None,
    ) -> list[ResultRecord]:
        """Store a run's results. The room derives everything it vouches for from the run:
        room, revisions, manifest and digest; the stamp always starts at ``checking``.
        A result becomes current only if the run is still active, its inputs still match,
        and no better result (higher tier, or a newer run of the same tier) is current.
        Anything else is kept as superseded history."""
        if actor.id != "conductor":
            raise Denied("only the conductor records solver results")
        run = self.run(rid)
        ids = [r.id for r in records]
        quantities = [r.quantity for r in records]
        if len(set(ids)) != len(ids) or len(set(quantities)) != len(quantities):
            raise Denied("a result batch repeats a result id or a quantity")
        active = run["status"] == RUN_ACTIVE
        eligible = active and self.run_is_current(rid)
        ops: Patch = []
        out: list[ResultRecord] = []
        results = cast(dict[str, dict[str, Any]], self.read("results") or {})
        for rec in records:
            quantity, value, unit = rec.quantity, rec.value, rec.unit
            if rec.id in results:
                raise Denied(f"result {rec.id} already exists")
            rec = rec.model_copy(
                update={
                    "room": self.id,
                    "case": run["case"],
                    "run_id": rid,
                    "quantity": quantity,
                    "value": value,
                    "unit": unit,
                    "tier": Tier(int(run["tier"])),
                    "input_revisions": run["inputRevisions"],
                    "manifest_digest": run["manifestDigest"],
                    "result_digest": result_digest(
                        quantity,
                        value,
                        unit,
                        rid,
                        run["manifestDigest"],
                        rec.artifacts,
                        kind=rec.kind,
                    ),
                    "stamp": Stamp.CHECKING,
                    "ledger_id": None,
                }
            )
            status = ResultStatus.SUPERSEDED
            replaces: str | None = None
            if eligible:
                rivals = {
                    oid: old
                    for oid, old in results.items()
                    if old.get("case") == rec.case
                    and old.get("quantity") == quantity
                    and old.get("status") == "current"
                }
                if not any(
                    self._outranks(old, int(rec.tier), int(run["openedSeq"]))
                    for old in rivals.values()
                ):
                    status = ResultStatus.CURRENT
                    for oid in rivals:
                        path = pointer("results", oid, "status")
                        ops.append({"op": "replace", "path": path, "value": "superseded"})
                        replaces = oid
            rec = rec.model_copy(update={"status": status, "replaces": replaces})
            ops.append({"op": "add", "path": pointer("results", rec.id), "value": rec.wire()})
            out.append(rec)
        if active:
            run_status = "done" if eligible else "late"
            ops.append(
                {"op": "replace", "path": pointer("runs", rid, "status"), "value": run_status}
            )
        meta = {"run": rid, "results": [r.id for r in out], "current": eligible}
        if verified is not None:  # one commit: the value never exists in state unchecked
            entry, verify = verified
            (only,) = out
            ops += self._ledger_ops(
                entry, verify, only, current=only.status == ResultStatus.CURRENT
            )
            meta["ledger"] = entry.id
        self._commit(actor, "run.results", ops, meta)
        return out

    @locked
    def fail_run(
        self, actor: Actor, rid: str, status: str, error: str, quantities: list[str]
    ) -> None:
        """A failed or timed-out solve. The values it would have replaced (results of older
        runs) are no longer current; results of newer runs are left alone."""
        if actor.id != "conductor":
            raise Denied("only the conductor records solver failures")
        if status not in ("failed", "timeout", "cancelled"):
            raise Denied(f"bad failure status {status}")
        run = self.run(rid)
        if run["status"] != RUN_ACTIVE:
            raise Conflict(f"run {rid} is already {run['status']}")
        ops: Patch = [
            {"op": "replace", "path": pointer("runs", rid, "status"), "value": status},
            {"op": "add", "path": pointer("runs", rid, "error"), "value": error},
        ]
        opened = int(run["openedSeq"])
        for oid, old in cast(dict[str, dict[str, Any]], self.read("results") or {}).items():
            if (
                old.get("case") != run["case"]
                or old.get("quantity") not in quantities
                or old.get("status") != "current"
            ):
                continue
            old_run = cast(dict[str, Any], self.read("runs", str(old.get("runId"))) or {})
            if int(old_run.get("openedSeq", 0)) < opened:
                ops.append(
                    {"op": "replace", "path": pointer("results", oid, "status"), "value": "stale"}
                )
                ops.append(
                    {"op": "replace", "path": pointer("results", oid, "stamp"), "value": "warning"}
                )
        self._commit(actor, "run.failed", ops, {"run": rid, "status": status, "error": error})

    def result(self, rid: str) -> ResultRecord:
        node = self.read("results", rid)
        if node is None:
            raise NotFound(f"no result {rid}")
        return ResultRecord.model_validate(node)

    def current_results(self, case: str) -> dict[str, ResultRecord]:
        out: dict[str, ResultRecord] = {}
        for node in cast(dict[str, Any], self.read("results") or {}).values():
            rec = ResultRecord.model_validate(node)
            if rec.case == case and rec.status == ResultStatus.CURRENT:
                out[rec.quantity] = rec
        return out

    # ledger (FR-RG-*) ------------------------------------------------------------------
    def _ledger_ops(
        self, entry: LedgerEntry, verify: Verifier, rec: ResultRecord, *, current: bool
    ) -> Patch:
        """The ledger entry for a result about to be stored, checked like admit_ledger."""
        if not verify(entry):
            raise Denied("ledger entry signature does not verify against the pinned key")
        s = entry.subject
        if (
            s.room != self.id
            or s.result_id != rec.id
            or s.run_id != rec.run_id
            or s.result_digest != rec.result_digest
            or s.manifest_digest != rec.manifest_digest
            or s.input_revisions != rec.input_revisions
            or s.quantity != rec.quantity
            or s.kind != rec.kind
            or s.value != rec.value
            or s.unit != rec.unit
            or ((s.kind != "number" or s.artifacts) and s.artifacts != rec.artifacts)
        ):
            raise Denied("ledger entry does not match the result")
        ops: Patch = [
            {
                "op": "add",
                "path": pointer("ledger", entry.id),
                "value": {**entry.wire(), "current": current},
            }
        ]
        if current:
            ops += [
                {
                    "op": "replace",
                    "path": pointer("results", rec.id, "stamp"),
                    "value": s.stamp.value,
                },
                {"op": "add", "path": pointer("results", rec.id, "ledgerId"), "value": entry.id},
            ]
        return ops

    @locked
    def admit_ledger(self, entry: LedgerEntry, verify: Verifier) -> bool:
        """Admit a referee entry. Returns True if it stamped the current result, False if it
        was kept only as history. Raises Denied for a bad signature or a mismatched subject."""
        if not verify(entry):
            raise Denied("ledger entry signature does not verify against the pinned key")
        s = entry.subject
        if s.room != self.id:
            raise Denied("ledger entry belongs to another room")
        rec = self.result(s.result_id)
        expected = result_digest(
            rec.quantity,
            rec.value,
            rec.unit,
            rec.run_id,
            rec.manifest_digest,
            rec.artifacts,
            kind=rec.kind,
        )
        if (
            rec.run_id != s.run_id
            or rec.result_digest != s.result_digest
            or rec.result_digest != expected
            or rec.manifest_digest != s.manifest_digest
            or rec.input_revisions != s.input_revisions
            or rec.case != s.case
            or rec.quantity != s.quantity
            or rec.kind != s.kind
            or rec.value != s.value
            or rec.unit != s.unit
            or ((s.kind != "number" or s.artifacts) and s.artifacts != rec.artifacts)
        ):
            raise Denied("ledger entry does not match the stored result")
        if self.read("ledger", entry.id) is not None:
            return bool(self.read("ledger", entry.id, "current"))
        current = rec.status == ResultStatus.CURRENT and rec.input_revisions == self.revisions(
            rec.case
        )
        value = {**entry.wire(), "current": current}
        ops: Patch = [{"op": "add", "path": pointer("ledger", entry.id), "value": value}]
        if current:
            ops += [
                {
                    "op": "replace",
                    "path": pointer("results", rec.id, "stamp"),
                    "value": s.stamp.value,
                },
                {"op": "add", "path": pointer("results", rec.id, "ledgerId"), "value": entry.id},
            ]
        meta = {"ledger": entry.id, "result": rec.id, "stamp": s.stamp.value, "current": current}
        self._commit(Actor.room_agent("referee"), "ledger.entry", ops, meta)
        return current

    def stamp_of(self, rid: str) -> Stamp:
        return self.result(rid).stamp

    # tier-2 queue (shown in state; the jobs themselves persist in the store) -------------
    @locked
    def set_queue_entry(self, job_id: str, entry: dict[str, Any]) -> None:
        ops: Patch = []
        if self.read("queue") is None:
            ops.append({"op": "add", "path": "/queue", "value": {}})
        ops.append(
            {
                "op": "add",
                "path": pointer("queue", job_id),
                "value": {**entry, "updatedSeq": self.seq + 1},
            }
        )
        self._commit(
            Actor.backend(), "queue.update", ops, {"job": job_id, "status": entry.get("status")}
        )

    # tensions (Decide together) --------------------------------------------------------
    @locked
    def open_tension(self, actor: Actor, case: str, payload: dict[str, Any]) -> str:
        if actor.id != "conductor":
            raise Denied("only the conductor opens a tension card")
        tid = new_id("ten")
        value = {"id": tid, "case": case, **payload}
        ops: Patch = [{"op": "add", "path": pointer("tensions", tid), "value": value}]
        self._commit(actor, "tension.open", ops, {"tension": tid, "case": case})
        return tid

    @locked
    def update_tension(self, actor: Actor, tid: str, key: str, value: Any) -> None:
        if actor.id not in ("conductor", "swarm"):
            raise Denied("only the conductor or the swarm updates a tension card")
        if self.read("tensions", tid) is None:
            raise NotFound(f"no tension {tid}")
        op = "replace" if self.read("tensions", tid, key) is not None else "add"
        ops: Patch = [{"op": op, "path": pointer("tensions", tid, key), "value": value}]
        self._commit(actor, "tension.update", ops, {"tension": tid, "key": key})

    # pinned surfaces (BUILD_SPEC section 8: pin, share, refresh) -----------------------
    # A pin keeps the refs-only plan, never a number, and the handles it names resolved to
    # stable ids (a handle name such as ``r1`` means something else on the next turn). The
    # bound surface is rebuilt from this record whenever it is published or refreshed.
    @locked
    def pin_surface(
        self,
        actor: Actor,
        plan: dict[str, Any],
        handles: dict[str, dict[str, Any]],
        origin: dict[str, Any],
        *,
        source: str = "",
        op_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an ad-hoc plan for its owner. ``handles`` maps each handle name the plan
        uses to what it stands for; the record keeps those and no others. Returns the pin.
        Called by the ``ui.pin`` op after the person confirmed the card."""
        owner = self._pin_actor(actor)
        if op_id is not None:
            prior = self.existing_op(actor, op_id)
            if prior is not None:
                return self.pin(cast(str, prior.meta["pin"]))
        parsed = AdHocPlan.model_validate(plan)
        used = list(dict.fromkeys(ref for block in parsed.blocks for ref in block.refs))
        kept: dict[str, dict[str, Any]] = {}
        for name in used:
            handle = handles.get(name)
            if handle is None:
                raise RoomError(f"the plan names {name!r}, which is not a handle", code="no_handle")
            if handle.get("kind") not in _PIN_HANDLE_KINDS or not isinstance(handle.get("id"), str):
                raise RoomError(f"{name!r} is not a handle that can be pinned", code="bad_handle")
            kept[name] = {
                key: handle[key]
                for key in _PIN_HANDLE_FIELDS
                if isinstance(handle.get(key), str) and handle[key] != ""
            }
        pins = cast(dict[str, dict[str, Any]], self.read("pins") or {})
        if len(pins) >= MAX_PINS_PER_ROOM:
            raise RoomError(
                f"the room holds at most {MAX_PINS_PER_ROOM} pinned views", code="limit"
            )
        if sum(1 for p in pins.values() if p.get("owner") == owner) >= MAX_PINS_PER_PERSON:
            raise RoomError(
                f"a person pins at most {MAX_PINS_PER_PERSON} views; unpin one first", code="limit"
            )
        cases = list(dict.fromkeys(h["case"] for h in kept.values() if h.get("case")))
        lanes = list(
            dict.fromkeys(
                f"{h['case']}/{h['lane']}" for h in kept.values() if h.get("case") and h.get("lane")
            )
        )
        pin_id = new_id("pin")
        record: dict[str, Any] = {
            "id": pin_id,
            "owner": owner,
            "title": parsed.title,
            "plan": parsed.wire(),
            "handles": kept,
            "origin": {k: str(v) for k, v in origin.items() if isinstance(v, str)},
            "case": cases[0] if cases else str(self.read("room", "activeCase") or ""),
            "lanes": lanes,
            "source": source,
            "pinnedSeq": self.seq + 1,
            "refreshedSeq": self.seq + 1,
            "shares": {},
        }
        ops: Patch = []
        if self.read("pins") is None:
            ops.append({"op": "add", "path": "/pins", "value": {}})
        ops.append({"op": "add", "path": pointer("pins", pin_id), "value": record})
        self._commit(actor, "surface.pin", ops, {"pin": pin_id, "owner": owner}, op_id)
        return record

    def pin(self, pin_id: str) -> dict[str, Any]:
        node = self.read("pins", pin_id)
        if node is None:
            raise NotFound(f"no pinned view {pin_id}")
        return cast(dict[str, Any], node)

    def pins_of(self, person: str) -> list[dict[str, Any]]:
        """The views a person owns or was shared, oldest first."""
        pins = cast(dict[str, dict[str, Any]], self.read("pins") or {})
        mine = [
            p
            for p in pins.values()
            if p.get("owner") == person or person in (p.get("shares") or {})
        ]
        return sorted(mine, key=lambda p: int(p.get("pinnedSeq", 0)))

    @locked
    def unpin_surface(self, actor: Actor, pin_id: str, op_id: str | None = None) -> None:
        owner = self._pin_actor(actor)
        if self.read("pins", pin_id) is None:
            return
        if self.pin(pin_id)["owner"] != owner:
            raise Denied("only the person who pinned a view takes it down")
        self._commit(
            actor,
            "surface.unpin",
            [{"op": "remove", "path": pointer("pins", pin_id)}],
            {"pin": pin_id},
            op_id,
        )

    @locked
    def mark_pin_refreshed(self, actor: Actor, pin_id: str, op_id: str | None = None) -> int:
        """Record that the stored refs were bound again against the room as it is now. Returns
        the seq the pin now reflects. Any seated person may refresh a view they can see."""
        person = self._pin_actor(actor)
        pin = self.pin(pin_id)
        if person != pin["owner"] and person not in (pin.get("shares") or {}):
            raise Denied("that view is not yours or shared with you")
        seq = self.seq + 1
        ops: Patch = [
            {"op": "replace", "path": pointer("pins", pin_id, "refreshedSeq"), "value": seq}
        ]
        self._commit(actor, "surface.refresh", ops, {"pin": pin_id}, op_id)
        return seq

    @locked
    def share_surface(
        self,
        actor: Actor,
        pin_id: str,
        recipients: list[str],
        op_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Share a pinned view with named, seated people. Returns one entry per recipient,
        ``{"recipient", "editable"}``, for the caller to journal once each. A recipient may edit
        through the view only when they own every lane it shows; otherwise it is read only.
        Called by the ``ui.share`` op after the person confirmed the card."""
        owner = self._pin_actor(actor)
        pin = self.pin(pin_id)
        if pin["owner"] != owner:
            raise Denied("only the person who pinned a view shares it")
        names = list(dict.fromkeys(recipients))
        if not names:
            raise RoomError("name at least one person to share with", code="no_recipient")
        if len(names) > MAX_SHARE_RECIPIENTS:
            raise RoomError(f"share with at most {MAX_SHARE_RECIPIENTS} people", code="limit")
        lanes = [str(ln) for ln in pin.get("lanes", [])]
        owners = self.lane_owners()
        entries: list[dict[str, Any]] = []
        for name in names:
            if name == owner:
                raise RoomError("the view is already yours", code="self_share")
            self._require_seat(name)
            editable = bool(lanes) and all(
                (owners.get(ln.split("/", 1)[0]) or {}).get(ln.split("/", 1)[1]) == name
                for ln in lanes
            )
            entries.append({"recipient": name, "editable": editable})
        seq = self.seq + 1
        ops: Patch = []
        for entry in entries:
            path = pointer("pins", pin_id, "shares", entry["recipient"])
            op = "replace" if self.read("pins", pin_id, "shares", entry["recipient"]) else "add"
            ops.append(
                {"op": op, "path": path, "value": {"editable": entry["editable"], "seq": seq}}
            )
        meta = {"pin": pin_id, "recipients": [e["recipient"] for e in entries]}
        self._commit(actor, "surface.share", ops, meta, op_id)
        return entries

    def _pin_actor(self, actor: Actor) -> str:
        """The person a pin or share is for. Room agents and the backend never pin."""
        person = actor.person_id
        if actor.kind not in ("person", "personal_agent") or person is None:
            raise Denied("only a person, or their own agent for them, pins and shares views")
        self._require_seat(person)
        return person


def personal_agent_for(person: str) -> str:
    return agent_id(person)
