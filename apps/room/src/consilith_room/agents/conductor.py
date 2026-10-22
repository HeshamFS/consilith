"""Generic snapshot-bound execution, result storage and evidence review.

Scientific schemas, workflows, formulas, and artifact projections are selected from trusted
installed extensions. This host owns identity, permissions, revisions, manifests and records.
A changed input snapshot makes an in-flight result historical rather than current.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from contextvars import ContextVar
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
from consilith_contracts.manifest_meta import ManifestMetaError, normalize_run_meta
from consilith_contracts.plugins import RunMeta
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
# The job a run belongs to. ``RunSupervisor`` and the tier-2 worker set it around the workflow;
# ``run_tool`` reads it and passes ``jobId`` to ``open_run``, so a job card joins
# job -> runs -> call-{runId} with no plugin change. Context variables follow awaits and tasks
# created inside the workflow, which is how it reaches ``run_tool`` through the workflow context.
current_job: ContextVar[str | None] = ContextVar("consilith_current_job", default=None)
ERROR_TEXT_CAP = 300


def error_text(exc: BaseException) -> str:
    """The real error text of a failed job: the ``RoomError`` or ``SolverError`` message, not the
    exception class name. Capped; a bare exception falls back to its class name."""
    text = str(getattr(exc, "message", "") or exc).strip()
    return (text or type(exc).__name__)[:ERROR_TEXT_CAP]


TIER_DEADLINE_S = {0: 10.0, 1: 30.0, 2: 600.0}
VERSION = "consilith-room/0.1.0"
# A plugin declares how long a (capability, mode) takes in ``runMeta``. The call may take a
# multiple of that estimate, never more than the operator's ceiling.
RUN_CEILING_S = 3600.0
DEADLINE_SLACK = 2.0
DEADLINE_GRACE_S = 30.0
# The mode a job was asked for, and a place for the job to learn which run it opened. The tier-2
# worker sets both around the workflow; a plain call sets neither.
declared_mode: ContextVar[str | None] = ContextVar("declared_mode", default=None)
job_run_sink: ContextVar[Callable[[str], None] | None] = ContextVar("job_run_sink", default=None)


def run_meta_for(room: RoomService, case: str, mode: str | None) -> RunMeta | None:
    """The ``runMeta`` an active plugin declares for ``case`` in ``mode``, if any."""
    if not mode:
        return None
    for plugin in room.read("room", "plugins") or []:
        raw = plugin.get("runMeta", plugin.get("run_meta"))
        if not raw:
            continue
        try:
            declared = normalize_run_meta(raw)
        except ManifestMetaError:
            continue
        found = declared.get(case, {}).get(mode)
        if found is not None:
            return found
    return None


def run_deadline_s(tier: int, meta: RunMeta | None, ceiling_s: float = RUN_CEILING_S) -> float:
    """The call deadline: the tier's default, raised to a declared estimate, under the ceiling."""
    deadline = TIER_DEADLINE_S.get(tier, 30.0)
    if meta is not None and meta.estimate_s:
        deadline = max(deadline, meta.estimate_s * DEADLINE_SLACK + DEADLINE_GRACE_S)
    return min(deadline, ceiling_s)


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
        run_ceiling_s: float = RUN_CEILING_S,
    ) -> None:
        self.run_ceiling_s = run_ceiling_s
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
            # A valid run grant (a card the person approved, still matching the inputs) skips
            # only this autonomy test; the tool allowlist below and the compute budget stay.
            if config.autonomy != Autonomy.RUN_SOLVERS and not room.run_grant_valid(
                requester, case
            ):
                raise Denied(
                    f"{requester.id} may not run solvers (autonomy {config.autonomy.value})"
                    + (
                        "; the approval no longer matches the inputs"
                        if requester.grant is not None
                        else ""
                    )
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
        run_meta = run_meta_for(room, case, declared_mode.get() or (context or {}).get("mode"))
        deadline = run_deadline_s(tier, run_meta, self.run_ceiling_s)
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
            # a declared deadline travels in the digested manifest, so the referee can trust it
            context={**(context or {}), "deadlineS": deadline} if run_meta else (context or {}),
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
            job_id=current_job.get(),
        )
        if (note := job_run_sink.get()) is not None:
            note(rid)
        # The solver sees who asked: the requesting agent, not always "conductor".
        caller = requester.id if requester.kind == "personal_agent" else "conductor"
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
                    caller=caller,
                    correlation_id=f"call-{rid}",
                    progress=progress,
                )
        except asyncio.CancelledError:
            # A cancelled job must not leave its run active. Re-raise so the task ends.
            with contextlib.suppress(RoomError):
                room.fail_run(CONDUCTOR, rid, "cancelled", "cancelled", [])
            raise
        except SolverTimeout as exc:
            room.fail_run(CONDUCTOR, rid, "timeout", exc.message, [])
            return rid, None, [], exc.message
        except SolverError as exc:
            room.fail_run(CONDUCTOR, rid, "failed", exc.message, [])
            return rid, None, [], exc.message
        except Exception as exc:  # anything else must not leave the run active either
            with contextlib.suppress(RoomError):
                room.fail_run(CONDUCTOR, rid, "failed", error_text(exc), [])
            raise
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
