"""Generic snapshot-bound execution, result storage and evidence review.

Scientific schemas, workflows, formulas, and artifact projections are selected from trusted
installed extensions. This host owns identity, permissions, revisions, manifests and records.
A changed input snapshot makes an in-flight result historical rather than current.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from consilith_contracts import (
    LedgerEntry,
    Provenance,
    ResultEnvelope,
    ResultRecord,
    ReviewItem,
    ReviewRequest,
    RunManifest,
    SpecError,
    Tier,
    Validity,
    digest,
    new_id,
)
from consilith_contracts.execution import SolveOutcome
from consilith_contracts.extensions import optional_extension, registry_operation
from consilith_contracts.review import LEDGER_MEDIA_TYPE, REVIEW_MEDIA_TYPE
from consilith_contracts.solver import inputs_hash

from ..domain.actors import Actor
from ..domain.errors import Denied, RoomError, SpecInvalid
from ..domain.plugin_access import (
    case_tools,
    replay_policy,
    require_case,
    require_extension,
    require_tool,
    room_pins,
)
from ..domain.service import RoomService
from ..platform.store import Store
from ..protocols.a2a_client import A2ACaller
from ..protocols.mcp_client import SolverError, SolverRegistry, SolverTimeout
from .artifact_projection import ArtifactProjection
from .output_payloads import prepare_outputs
from .status import acting
from .tier0 import evaluate

CONDUCTOR = Actor.room_agent("conductor")
TIER_DEADLINE_S = {0: 10.0, 1: 30.0, 2: 600.0}
VERSION = "consilith-room/0.1.0"


class Conductor:
    def __init__(
        self,
        store: Store,
        registry: SolverRegistry,
        caller: A2ACaller,
        *,
        referee_base: str,
        referee_token: str | None,
        verify: Callable[[LedgerEntry], bool],
    ) -> None:
        self.store = store
        self.registry = registry
        self.caller = caller
        self.referee_base = referee_base
        self.referee_token = referee_token
        self.verify = verify

    def authorize(self, room: RoomService, requester: Actor, case: str) -> None:
        """Validate identity, installed package and agent tool policy before any execution."""
        require_case(room, case)
        if requester.person_id is None or room.read("room", "people", requester.person_id) is None:
            raise Denied("a workflow is requested by a seated person or their agent")
        if requester.kind == "personal_agent":
            from consilith_contracts import Autonomy

            config = room.agent_config(requester.id)
            if config.autonomy != Autonomy.RUN_SOLVERS:
                raise Denied(
                    f"{requester.id} may not run solvers (autonomy {config.autonomy.value})"
                )
            for tool in optional_extension("workflow_required_tools", case) or []:
                if not config.tool_allowed(tool):
                    raise Denied(f"{requester.id} may not call {tool}")

    @registry_operation
    async def solve(
        self, room: RoomService, requester: Actor, case: str, **options: Any
    ) -> SolveOutcome:
        from consilith_contracts.extensions import extension

        from .execution import RoomWorkflowContext

        self.authorize(room, requester, case)
        require_extension(room, "workflows", case, case=case)
        try:
            workflow = extension("workflows", case)
            return await workflow(RoomWorkflowContext(self, room, requester, case), **options)
        except SpecError as exc:
            raise SpecInvalid(exc.field, exc.message) from exc

    def __getattr__(self, name: str) -> Any:
        """Legacy Python callers resolve through the same configured extension registry."""
        from consilith_contracts.extensions import extension

        from .execution import RoomWorkflowContext

        if name.startswith("solve_"):
            case = name.removeprefix("solve_")
            extension("workflows", case)

            async def run(room: RoomService, requester: Actor, **options: Any) -> SolveOutcome:
                return await self.solve(room, requester, case, **options)

            return run
        if name.endswith("_spec"):
            case = name.removesuffix("_spec")

            @registry_operation
            def read_spec(room: RoomService) -> Any:
                require_case(room, case)
                require_extension(room, "spec_readers", case, case=case)
                reader = extension("spec_readers", case)
                return reader(RoomWorkflowContext(self, room, CONDUCTOR, case))

            return read_spec
        raise AttributeError(name)

    def _run_requester(
        self,
        room: RoomService,
        case: str,
        tool: str,
        requester: Actor,
        dependency: bool,
        tier: int,
        estimate_s: float = 0.0,
    ) -> Actor:
        """Delegate declared prerequisites without bypassing explicit personal policy."""
        if not dependency or tool not in (optional_extension("workflow_dependencies", case) or []):
            return requester
        self.authorize(room, requester, case)
        if requester.kind == "personal_agent":
            config = room.agent_config(requester.id)
            if tool in config.tools_denied:
                raise Denied(f"{requester.id} may not call {tool}")
            if tier < 2 and estimate_s > config.compute_budget_s:
                raise Denied(
                    "dependency exceeds the requesting agent's compute budget", route="tier2"
                )
        return Actor.room_agent("conductor", initiator=requester.person_id)

    # one tool call as one run ----------------------------------------------------------
    @registry_operation
    async def run_tool(
        self,
        room: RoomService,
        case: str,
        tool: str,
        arguments: dict[str, Any],
        requester: Actor,
        tier: int,
        *,
        snapshot: dict[str, str],
        context: dict[str, Any] | None = None,
        estimate_s: float = 0.0,
        rename: dict[str, str] | None = None,
        keep: set[str] | None = None,
        progress: bool = False,
        dependency: bool = False,
    ) -> tuple[str, ResultEnvelope | None, list[ResultRecord], str | None]:
        require_case(room, case)
        require_tool(room, tool)
        if tool not in case_tools(room, case):
            raise Denied("tool is outside the capability plugin dependencies")
        presenter = optional_extension("artifact_presenters", tool)
        if presenter is not None:
            require_extension(room, "artifact_presenters", tool, case=case)
            if isinstance(presenter, dict):
                presenter = ArtifactProjection.model_validate(presenter)
        info = self.registry.tools.get(tool)
        manifest = RunManifest(
            tool=tool,
            server=info.server if info else "unknown",
            package_pins=room_pins(room),
            replay_policy=replay_policy(room, case, tool),
            capability_version=info.server_version if info else None,
            schema_digest=digest(room.case_definitions()[case]),
            checker_profile=case,
            arguments=arguments,
            spec_revision=digest(snapshot),
            input_revisions=snapshot,
            context=context or {},
            outputs={k: v for k, v in (rename or {}).items() if k != v},
        )
        mdigest = digest(manifest)
        self.store.put_manifest(mdigest, manifest.wire())
        rid = room.open_run(
            CONDUCTOR,
            case,
            tool,
            tier,
            mdigest,
            self._run_requester(room, case, tool, requester, dependency, tier, estimate_s),
            estimate_s=estimate_s,
            input_revisions=snapshot,
        )
        deadline = TIER_DEADLINE_S.get(tier, 30.0)
        if requester.kind == "personal_agent" and tier < 2:
            deadline = min(deadline, room.agent_config(requester.id).compute_budget_s)
        try:
            async with (
                acting(room, "conductor", "waiting", f"solvers: {tool}"),
                acting(room, "solvers", "working", tool),
            ):
                call = await self.registry.call(
                    tool,
                    arguments,
                    deadline_s=deadline,
                    room=room.id,
                    caller="conductor",
                    correlation_id=f"call-{rid}",
                    progress=progress,
                )
        except SolverTimeout as exc:
            room.fail_run(CONDUCTOR, rid, "timeout", exc.message, [])
            return rid, None, [], exc.message
        except SolverError as exc:
            room.fail_run(CONDUCTOR, rid, "failed", exc.message, [])
            return rid, None, [], exc.message
        env = call.envelope
        prepared, blobs = prepare_outputs(env, presenter)
        for ref, payload in blobs.values():
            self.store.put_artifact(ref.sha256, ref.media_type, payload)
        records = [
            ResultRecord(
                id=new_id("res"),
                room=room.id,
                case=case,
                run_id=rid,
                quantity=(rename or {}).get(q.name, q.name),
                kind=q.kind,
                value=q.value,
                unit=q.unit,
                tier=env.tier,
                illustrative=env.illustrative,
                artifacts=q.artifacts,
                provenance=env.provenance,
                validity=env.validity,
                manifest_digest=mdigest,
                input_revisions={},
                result_digest="",
            )
            for q in prepared
            if keep is None or q.name in keep
        ]
        return rid, env, room.record_results(CONDUCTOR, rid, records), None

    @registry_operation
    def tier0(
        self,
        room: RoomService,
        case: str,
        requester: Actor,
        name: str,
        inputs: dict[str, float],
        validity: Validity,
        *,
        snapshot: dict[str, str],
        context: dict[str, Any] | None = None,
        dependency: bool = False,
    ) -> list[ResultRecord]:
        """Deterministic tier-0 arithmetic as its own run, so every number has provenance.
        It inherits the snapshot of the results it was computed from. The formula comes from
        ``tier0.FORMULAS``, so ``reproduce`` can run the same one again."""
        require_case(room, case)
        require_tool(room, name)
        if name not in case_tools(room, case):
            raise Denied("tool is outside the capability plugin dependencies")
        require_extension(room, "formulas", name, case=case)
        if optional_extension("formula_versions", name) is not None:
            require_extension(room, "formula_versions", name, case=case)
        version = optional_extension("formula_versions", name) or VERSION
        manifest = RunManifest(
            tool=name,
            server="room",
            package_pins=room_pins(room),
            replay_policy=replay_policy(room, case, name),
            schema_digest=digest(room.case_definitions()[case]),
            checker_profile=case,
            arguments=inputs,
            spec_revision=digest(snapshot),
            input_revisions=snapshot,
            solver_version=version,
            context=context or {},
        )
        mdigest = digest(manifest)
        self.store.put_manifest(mdigest, manifest.wire())
        rid = room.open_run(
            CONDUCTOR,
            case,
            name,
            0,
            mdigest,
            self._run_requester(room, case, name, requester, dependency, 0),
            input_revisions=snapshot,
        )
        try:
            values = evaluate(name, inputs, context)
            if not values:
                raise ValueError("the formula produced no quantities")
            prov = Provenance(
                tool=name, solver_version=version, inputs_hash=inputs_hash(inputs), wall_time_s=0.0
            )
            records = [
                ResultRecord(
                    id=new_id("res"),
                    room=room.id,
                    case=case,
                    run_id=rid,
                    quantity=q,
                    value=v,
                    unit=u,
                    tier=Tier.HAND_CALC,
                    provenance=prov,
                    validity=validity,
                    manifest_digest=mdigest,
                    input_revisions={},
                    result_digest="",
                )
                for q, v, u in values
            ]
            return room.record_results(CONDUCTOR, rid, records)
        except Exception as exc:
            room.fail_run(CONDUCTOR, rid, "failed", str(exc), [])
            raise

    # the referee ----------------------------------------------------------------------------
    async def review(
        self,
        room: RoomService,
        run_id: str,
        records: list[ResultRecord],
        out: SolveOutcome | None = None,
    ) -> list[bool]:
        stored = [room.result(r.id) for r in records]
        if not stored:
            return []
        if any(
            r.run_id != run_id
            or r.case != stored[0].case
            or r.manifest_digest != stored[0].manifest_digest
            for r in stored
        ):
            raise Denied("review records must belong to the requested canonical run")
        manifest_raw = self.store.manifest(stored[0].manifest_digest) or {}
        req = ReviewRequest(
            room=room.id,
            case=stored[0].case,
            run_id=run_id,
            manifest=RunManifest.model_validate(manifest_raw),
            items=[
                ReviewItem(
                    result_id=r.id,
                    quantity=r.quantity,
                    kind=r.kind,
                    value=r.value,
                    unit=r.unit,
                    result_digest=r.result_digest,
                    input_revisions=r.input_revisions,
                    manifest_digest=r.manifest_digest,
                    tier=r.tier,
                    artifacts=r.artifacts,
                )
                for r in stored
            ],
        )
        async with acting(room, "conductor", "waiting", "referee"):
            reply = await self.caller.send(
                room.id,
                sender="conductor",
                receiver="referee",
                base=self.referee_base,
                data=req.wire(),
                media_type=REVIEW_MEDIA_TYPE,
                token=self.referee_token,
            )
        admitted: list[bool] = []
        for block in reply.data:
            for raw in block.get("entries", []):
                entry = LedgerEntry.model_validate(raw)
                try:
                    admitted.append(room.admit_ledger(entry, self.verify))
                    if out is not None:
                        out.ledger.append(entry.id)
                except RoomError:
                    admitted.append(False)
        if not reply.data and out is not None:
            out.error = (
                f"referee did not answer with a ledger ({reply.state}); "
                f"{LEDGER_MEDIA_TYPE} expected"
            )
        return admitted
