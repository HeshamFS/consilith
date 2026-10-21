"""Reproduce a stored result from its run manifest alone, in a fresh process (FR-RG-7, NFR-7).

The manifest is content-addressed, so it is first checked against the digest the result
names. Then the same computation runs again, from the manifest only:

- a solver tool: a fresh solver process is started and called once with the same arguments;
- an analysis program: evaluated again in a fresh child process;
- a room-side tier-0 formula: the same formula (``tier0.FORMULAS``) in a fresh child process.

The value must match the pinned plugin replay policy, in the stored unit and version.
Nothing is stored: the outcome is logged as ``result.reproduced`` and returned.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from typing import Any

from consilith_contracts import RunManifest, digest
from consilith_contracts.analysis import AnalysisError, run_isolated
from consilith_contracts.extensions import (
    all_extensions,
    installed_pins,
    optional_extension,
    package_for,
    registry_operation,
)
from consilith_contracts.plugins import ReplayPolicy
from consilith_contracts.results import result_binding_digest
from consilith_contracts.units import UnitError, convert

from ..domain.service import RoomService
from ..protocols.fresh import call_in_fresh_process
from ..protocols.journal import Journal
from ..protocols.mcp_client import SolverError
from .artifact_projection import ArtifactProjection
from .conductor import CONDUCTOR, TIER_DEADLINE_S, VERSION, Conductor
from .output_payloads import prepare_outputs
from .tier0 import evaluate


def same(a: float, b: float, policy: ReplayPolicy | None = None) -> bool:
    policy = policy or ReplayPolicy()
    return a == b or math.isclose(
        a, b, rel_tol=policy.relative_tolerance, abs_tol=policy.absolute_tolerance
    )


def _tier0_fresh(name: str, inputs: dict[str, float], ctx: dict[str, Any]) -> list[Any]:
    # The extension proxy starts a fresh isolated invocation; the host imports no implementation.
    return evaluate(name, inputs, ctx)


@registry_operation
async def reproduce(
    conductor: Conductor,
    room: RoomService,
    result_id: str,
    *,
    solver_apps: dict[str, str],
    journal: Journal | None,
) -> dict[str, Any]:
    rec = room.result(result_id)
    out: dict[str, Any] = {
        "result": rec.id,
        "quantity": rec.quantity,
        "stored": {"value": rec.value, "unit": rec.unit},
        "manifest": rec.manifest_digest,
    }
    if rec.result_digest != result_binding_digest(
        rec.quantity,
        rec.value,
        rec.unit,
        rec.run_id,
        rec.manifest_digest,
        rec.artifacts,
        kind=rec.kind,
    ):
        return _done(
            room, {**out, "reproduced": False, "reason": "the stored output binding differs"}
        )
    for ref in rec.artifacts:
        stored = conductor.store.artifact(ref.sha256)
        if (
            stored is None
            or len(stored[1]) != ref.size_bytes
            or hashlib.sha256(stored[1]).hexdigest() != ref.sha256
        ):
            return _done(
                room,
                {
                    **out,
                    "reproduced": False,
                    "reason": "the stored artifact bytes differ or are missing",
                },
            )
    raw = conductor.store.manifest(rec.manifest_digest)
    if raw is None:
        return _done(room, {**out, "reproduced": False, "reason": "the manifest is missing"})
    manifest = RunManifest.model_validate(raw)
    if digest(manifest) != rec.manifest_digest:
        return _done(room, {**out, "reproduced": False, "reason": "the manifest digest differs"})
    installed = {pin.entry_point: pin for pin in installed_pins()}
    if any(installed.get(pin.entry_point) != pin for pin in manifest.package_pins):
        return _done(
            room,
            {
                **out,
                "reproduced": False,
                "reason": "the pinned scientific package version is unavailable",
            },
        )
    if manifest.server == "room":
        kind, name = (
            ("analysis_engines", "default")
            if manifest.tool == "analysis.run"
            else ("formulas", manifest.tool)
        )
        provider = package_for(kind, name)
        if provider is None or provider not in manifest.package_pins:
            return _done(
                room,
                {
                    **out,
                    "reproduced": False,
                    "reason": "the historical manifest does not pin this evaluator",
                },
            )
    out["tool"] = manifest.tool
    version = VERSION  # the room engine; a solver call below replaces it with the solver's
    if manifest.server == "room" and manifest.tool == "analysis.run":
        pin = package_for("analysis_engines", "default")
        version = pin.version if pin else VERSION
        args = manifest.arguments
        inputs = {k: (float(v["value"]), str(v["unit"])) for k, v in args["inputs"].items()}
        try:
            value, _ = await asyncio.to_thread(run_isolated, str(args["source"]), inputs)
        except AnalysisError as exc:
            return _done(room, {**out, "reproduced": False, "reason": str(exc)})
        again, unit, how = value.magnitude, value.unit, "evaluated again in a fresh process"
    elif manifest.server == "room":
        if manifest.tool not in all_extensions("formulas"):
            return _done(
                room, {**out, "reproduced": False, "reason": f"no formula {manifest.tool}"}
            )
        version = optional_extension("formula_versions", manifest.tool) or VERSION
        inputs = {k: float(v) for k, v in manifest.arguments.items()}
        values = await asyncio.to_thread(
            _tier0_fresh, manifest.tool, inputs, dict(manifest.context)
        )
        found = next(((v, u) for q, v, u in values if q == rec.quantity), None)
        if found is None:
            return _done(room, {**out, "reproduced": False, "reason": "the formula lacks it"})
        again, unit = found
        how = "the same tier-0 formula in a fresh process"
    else:
        app_ref = solver_apps.get(manifest.server)
        # Historical manifests without this field can replay a scalar, but cannot establish
        # that the numerical service implementation matches the original computation.
        out["implementationIdentity"] = (
            "unrecorded" if manifest.capability_version is None else "not_matched"
        )
        name = next(
            (src for src, dst in manifest.outputs.items() if dst == rec.quantity), rec.quantity
        )
        try:
            if app_ref is None:
                info = conductor.registry.tools.get(manifest.tool)
                if manifest.capability_version is not None and (
                    info is None or info.server_version != manifest.capability_version
                ):
                    raise SolverError(
                        manifest.tool, "the historical solver implementation identity differs"
                    )
                call = await conductor.registry.call(
                    manifest.tool,
                    manifest.arguments,
                    deadline_s=TIER_DEADLINE_S.get(int(rec.tier), 30.0),
                    room=room.id,
                    caller="conductor",
                    correlation_id=f"reproduce-{rec.id}",
                )
                how = "registered service replay, same arguments (not an isolated fresh process)"
            else:
                call = await call_in_fresh_process(
                    app_ref,
                    manifest.server,
                    manifest.tool,
                    manifest.arguments,
                    deadline_s=TIER_DEADLINE_S.get(int(rec.tier), 30.0),
                    journal=journal,
                    room=room.id,
                    correlation_id=f"reproduce-{rec.id}",
                    expected_server_version=manifest.capability_version,
                )
                how = f"{manifest.tool} in a fresh {manifest.server} process, same arguments"
            if manifest.capability_version is not None:
                out["implementationIdentity"] = "matched"
        except SolverError as exc:
            return _done(room, {**out, "reproduced": False, "reason": exc.message})
        presenter = optional_extension("artifact_presenters", manifest.tool)
        if presenter is not None:
            owner = package_for("artifact_presenters", manifest.tool)
            if owner is None or owner not in manifest.package_pins:
                return _done(
                    room,
                    {**out, "reproduced": False, "reason": "the artifact presenter is not pinned"},
                )
            if isinstance(presenter, dict):
                presenter = ArtifactProjection.model_validate(presenter)
        prepared, _ = prepare_outputs(call.envelope, presenter)
        q = next((x for x in prepared if x.name == name), None)
        if q is None:
            return _done(room, {**out, "reproduced": False, "reason": f"no {name} in the answer"})
        artifact_match = q.artifacts == rec.artifacts
        out["artifactMatch"] = artifact_match
        if q.kind != rec.kind:
            return _done(room, {**out, "reproduced": False, "reason": "the output kind differs"})
        if rec.kind != "number":
            return _done(
                room,
                {
                    **out,
                    "kind": rec.kind,
                    "again": {"value": q.value, "artifacts": [a.wire() for a in q.artifacts]},
                    "how": how,
                    "reproduced": digest(q.value) == digest(rec.value)
                    and artifact_match
                    and call.envelope.provenance.solver_version == rec.provenance.solver_version,
                },
            )
        if not isinstance(q.value, (int, float)):
            return _done(
                room, {**out, "reproduced": False, "reason": "the numerical payload is invalid"}
            )
        again, unit = q.value, q.unit
        version = call.envelope.provenance.solver_version
    if version != rec.provenance.solver_version:
        out["solverVersion"] = {"stored": rec.provenance.solver_version, "now": version}
    if rec.kind != "number" or not isinstance(rec.value, (int, float)):
        return _done(
            room, {**out, "reproduced": False, "reason": "the evaluator only supports numbers"}
        )
    try:
        again_in_stored = convert(again, unit, rec.unit)
    except UnitError as exc:
        return _done(room, {**out, "reproduced": False, "reason": str(exc)})
    return _done(
        room,
        {
            **out,
            "again": {"value": again_in_stored, "unit": rec.unit},
            "how": how,
            "reproduced": same(again_in_stored, rec.value, manifest.replay_policy)
            and "solverVersion" not in out
            and out.get("artifactMatch", True),
        },
    )


def _done(room: RoomService, out: dict[str, Any]) -> dict[str, Any]:
    room.note(
        CONDUCTOR,
        "result.reproduced",
        {k: out[k] for k in ("result", "quantity", "reproduced") if k in out},
    )
    return out


__all__ = ["reproduce", "same"]
