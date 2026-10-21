"""Host adapters for neutral plugin execution ports.

Only this adapter sees room services and actors. Plugins get snapshots and
explicit gated operations; they never receive the room, store, requester or signing key.
"""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from typing import Any, cast

from consilith_contracts import (
    Autonomy,
    MemoryScope,
    PatchOp,
    ProposalStatus,
    ResultRecord,
    SpecError,
    Validity,
    validate_lane,
)
from consilith_contracts.execution import SolveOutcome

from ..domain.actors import Actor
from ..domain.errors import Denied
from ..domain.paths import twin_lane
from ..domain.plugin_access import case_tools, require_case, require_tool
from ..domain.service import RoomService


class RoomWorkflowContext:
    def __init__(self, conductor: Any, room: RoomService, requester: Actor, case: str) -> None:
        require_case(room, case)
        self._conductor = conductor
        self._room = room
        self._requester = requester
        self._case = case
        with room.lock:
            self._model = deepcopy(room.read("twin", case) or {})
            self._snapshot = dict(room.revisions(case))
            self._results = dict(room.current_results(case))

    def snapshot(self, required: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, str]]:
        descriptor = self._room.case_definitions()[self._case]
        for lane in required:
            validate_lane(descriptor, lane, self._model.get(lane, {}), require=True)
        self._room.require_ready(self._case)
        return deepcopy(self._model), dict(self._snapshot)

    def artifact(self, digest: str) -> bytes | None:
        def references(value: Any) -> set[str]:
            if isinstance(value, dict):
                return {
                    str(v)
                    for k, v in value.items()
                    if k in ("artifact", "sha256") and isinstance(v, str)
                } | set().union(*(references(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(references(v) for v in value))
            return set()

        from ..domain.artifacts import owns_artifact

        if digest not in references(self._model) or not owns_artifact(
            self._room, self._conductor.store, digest
        ):
            return None
        found = self._conductor.store.artifact(digest)
        return None if found is None else found[1]

    def current_results(self) -> dict[str, ResultRecord]:
        return dict(self._results)

    def result(self, result_id: str) -> ResultRecord:
        record = self._room.result(result_id)
        if record.case != self._case:
            raise Denied("result is outside the bound capability")
        return record

    def _check_snapshot(self, snapshot: dict[str, str]) -> None:
        if snapshot != self._snapshot:
            raise SpecError("snapshot", "workflow changed its bound input revisions")

    async def run_tool(
        self,
        tool: str,
        arguments: dict[str, Any],
        tier: int,
        *,
        dependency: bool = False,
        snapshot: dict[str, str],
        **options: Any,
    ) -> Any:
        self._check_snapshot(snapshot)
        if tool not in case_tools(self._room, self._case):
            raise Denied("tool is outside the bound plugin capability")
        from consilith_contracts.extensions import optional_extension

        allowed_dependencies = optional_extension("workflow_dependencies", self._case) or []
        dependency = dependency and tool in allowed_dependencies
        return await self._conductor.run_tool(
            self._room,
            self._case,
            tool,
            arguments,
            self._requester,
            tier,
            dependency=dependency,
            snapshot=snapshot,
            **options,
        )

    def formula(
        self,
        name: str,
        inputs: dict[str, float],
        validity: Validity,
        *,
        snapshot: dict[str, str],
        **options: Any,
    ) -> list[ResultRecord]:
        self._check_snapshot(snapshot)
        if name not in case_tools(self._room, self._case):
            raise Denied("formula is outside the bound plugin capability")
        from consilith_contracts.extensions import optional_extension

        dependencies = optional_extension("workflow_dependencies", self._case) or []
        return self._conductor.tier0(
            self._room,
            self._case,
            self._requester,
            name,
            inputs,
            validity,
            dependency=name in dependencies,
            snapshot=snapshot,
            **options,
        )

    async def review(self, records: list[ResultRecord], outcome: SolveOutcome) -> SolveOutcome:
        records = [self.result(record.id) for record in records]
        slots = asyncio.Semaphore(4)

        async def review_run(run_id: str) -> SolveOutcome:
            partial = SolveOutcome()
            async with slots:
                await self._conductor.review(
                    self._room,
                    run_id,
                    [r for r in records if r.run_id == run_id],
                    partial,
                )
            return partial

        reviewed = await asyncio.gather(
            *(review_run(rid) for rid in dict.fromkeys(r.run_id for r in records)),
            return_exceptions=True,
        )
        for result in reviewed:
            if isinstance(result, BaseException):
                raise result
        for partial in cast(list[SolveOutcome], reviewed):
            outcome.ledger.extend(partial.ledger)
            if partial.error is not None:
                outcome.error = partial.error
        return outcome


class RoomProposalContext:
    def __init__(self, room: RoomService, case: str, propose: Any) -> None:
        require_case(room, case)
        self._room = room
        self._case = case
        self._propose = propose
        with room.lock:
            self.model = deepcopy(room.read("twin", case) or {})
            self.owners = dict(room.lane_owners().get(case, {}))
            self.proposals = deepcopy(
                [p for p in (room.read("proposals") or {}).values() if p.get("case") == case]
            )
        self._case_agents = {self.agent_for(owner) for owner in self.owners.values() if owner}

    def agent_for(self, owner: str) -> str:
        from ..domain.actors import agent_id

        return agent_id(owner)

    async def propose(
        self, agent: str, case: str, lane: str, patch: list[Any], reason: str, evidence: list[str]
    ) -> Any:
        self._authorize_agent(agent)
        self._check_patch(lane, patch)
        if case != self._case:
            raise SpecError("case", "policy attempted a different capability")
        return await self._propose(self._room, agent, case, lane, patch, reason, evidence)

    def _authorize_agent(self, aid: str) -> None:
        if aid not in self._case_agents:
            raise Denied("policy actor is outside the bound capability")

    def _check_patch(self, lane: str, patch: list[Any]) -> None:
        for raw in patch:
            op = raw if isinstance(raw, PatchOp) else PatchOp.model_validate(raw)
            target = twin_lane(op.path, self._room.case_definitions())
            if target is None or target.case != self._case or target.lane != lane:
                raise Denied("policy patch is outside its bound case and lane")
            if op.from_ is not None:
                origin = twin_lane(op.from_, self._room.case_definitions())
                if origin is None or origin.case != self._case or origin.lane != lane:
                    raise Denied("policy patch source is outside its bound case and lane")


class RoomAgentContext(RoomProposalContext):
    def __init__(
        self, kernel: Any, room: RoomService, case: str, actor_id: str | None = None
    ) -> None:
        super().__init__(room, case, kernel._case_propose)
        self._kernel = kernel
        self._actor_id = actor_id
        self._opened_tensions: list[str] = []
        self._started = time.monotonic()
        self._case = case
        with room.lock:
            self.warnings = deepcopy(
                [w for w in (room.read("warnings") or {}).values() if w.get("case") == case]
            )
            self.tensions = deepcopy(
                [t for t in (room.read("tensions") or {}).values() if t.get("case") == case]
            )
            self.revisions = dict(room.revisions(case))

    def config(self, aid: str) -> Any:
        if aid not in self._case_agents:
            raise Denied("agent is outside the bound capability")
        return self._room.agent_config(aid)

    def lessons(self, aid: str) -> list[Any]:
        if aid not in self._case_agents:
            raise Denied("agent is outside the bound capability")
        return [
            lesson
            for lesson in self._room.lessons_for(aid)
            if (lesson.scope != MemoryScope.PERSONAL or aid == self._actor_id)
            and (
                lesson.scope != MemoryScope.CASE or lesson.holder == f"{self._room.id}:{self._case}"
            )
        ]

    def has_proposals(self) -> bool:
        return any(p.get("status") == ProposalStatus.AWAITING_OWNER for p in self.proposals)

    def _authorize_agent(self, aid: str) -> None:
        self._actor(aid)

    def _actor(self, aid: str) -> Actor:
        if aid != self._actor_id or aid not in self._case_agents:
            raise Denied("policy cannot assume a different actor identity")
        return Actor.agent_of(str(self._room.agent(aid)["owner"]))

    def warn(self, aid: str, to: str, lane: str, message: str, evidence: list[str]) -> None:
        self._room.warn(self._actor(aid), to, self._case, lane, message, evidence=evidence)

    async def resolve(
        self, aid: str, owner: str, rec: ResultRecord, results: dict[str, ResultRecord]
    ) -> list[str]:
        self._actor(aid)
        if rec.case != self._case or any(r.case != self._case for r in results.values()):
            raise Denied("policy result is outside the bound capability")
        return await self._kernel.resolve_tension(self._room, aid, owner, rec, results)

    def open_tension(self, values: dict[str, Any]) -> str:
        tid = self._room.open_tension(Actor.room_agent("conductor"), self._case, values)
        self._opened_tensions.append(tid)
        return tid

    def _deny_pending(self, reason: str) -> None:
        # Only this invocation's new tensions are affected; concurrent work is untouched.
        for tid in self._opened_tensions:
            if self._room.read("tensions", tid, "status") == "exploring":
                self._room.update_tension(Actor.room_agent("conductor"), tid, "status", "blocked")
                self._room.update_tension(
                    Actor.room_agent("conductor"), tid, "blockedReason", reason
                )

    def update_tension(self, tid: str, key: str, value: Any, *, actor: str = "conductor") -> None:
        tension = self._room.read("tensions", tid) or {}
        if tension.get("case") != self._case or actor not in ("conductor", "swarm"):
            raise Denied("tension mutation is outside the bound capability")
        self._room.update_tension(Actor.room_agent(actor), tid, key, value)

    def workflow(self) -> RoomWorkflowContext:
        if self._actor_id is None:
            raise Denied("policy workflow requires a bound requesting agent")
        return RoomWorkflowContext(
            self._kernel.conductor, self._room, self._actor(self._actor_id), self._case
        )

    async def tool(self, tool: str, args: dict[str, Any], **options: Any) -> Any:
        from consilith_contracts.execution import PluginExecutionError

        from ..protocols.mcp_client import SolverError

        if self._actor_id is None:
            raise Denied("policy tool execution requires a bound requesting agent")
        self._actor(self._actor_id)
        require_tool(self._room, tool)
        if tool not in case_tools(self._room, self._case):
            raise Denied("policy tool is outside the bound plugin")
        # A policy cannot supply an actor or gain authority from its own caller string.
        from consilith_contracts.extensions import optional_extension

        dependencies = optional_extension("workflow_dependencies", self._case) or []
        eligible = [
            aid
            for aid in sorted(self._case_agents)
            if (self._actor_id is None or aid == self._actor_id)
            and self.config(aid).autonomy == Autonomy.RUN_SOLVERS
            and tool not in self.config(aid).tools_denied
            and (self.config(aid).tool_allowed(tool) or tool in dependencies)
        ]
        if not eligible:
            raise Denied("no participating agent is permitted to run this policy tool")
        caller = eligible[0]
        remaining = self.config(caller).compute_budget_s - (time.monotonic() - self._started)
        if remaining <= 0:
            raise Denied("policy compute budget exhausted")
        deadline = min(float(options.get("deadline_s", remaining)), remaining)
        try:
            return await self._kernel.conductor.registry.call(
                tool, args, room=self._room.id, deadline_s=deadline, caller=caller
            )
        except SolverError as exc:
            raise PluginExecutionError(exc.message) from exc

    def activity(self, aid: str, status: str, detail: str) -> Any:
        from .status import acting

        if aid not in ("conductor", "swarm", "solvers"):
            self._actor(aid)
        return acting(self._room, aid, status, detail)

    def public_skip(self, skip: dict[str, Any], lessons: list[Any]) -> dict[str, Any]:
        from .kernel import _public_skip

        return _public_skip(skip, lessons)

    async def edit_or_propose(self, aid: str, lane: str, patch: list[Any], reason: str) -> bool:
        from consilith_contracts import at_least

        from ..domain.errors import LaneNotOwned

        self._actor(aid)
        self._check_patch(lane, patch)
        if at_least(self.config(aid).autonomy, Autonomy.EDIT_MY_LANES):
            try:
                self._room.edit_twin(self._actor(aid), patch, reason)
                return True
            except LaneNotOwned:
                pass
        await self.propose(aid, self._case, lane, patch, reason, [])
        return False

    def ruled_out(
        self, aid: str, patch: list[Any], overrides: set[str] | None = None
    ) -> str | None:
        self._actor(aid)
        for raw in patch:
            op = raw if isinstance(raw, PatchOp) else PatchOp.model_validate(raw)
            target = twin_lane(op.path, self._room.case_definitions())
            if target is None or target.case != self._case:
                raise Denied("policy patch is outside the bound capability")
            self._check_patch(target.lane, [op])
        return self._kernel.ruled_out(self._room, aid, patch, overrides)

    def note(self, aid: str, event: str, values: dict[str, Any]) -> None:
        self._room.note(self._actor(aid), event, values)
