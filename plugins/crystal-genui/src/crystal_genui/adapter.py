"""Optional Consilith adapter. Scientific code executes in the separate MCP service."""

from __future__ import annotations

import base64
import hashlib

from consilith_contracts import AgentConfig, Autonomy, CaseDef, Check, FieldDef, LaneDef
from consilith_contracts.base import digest
from consilith_contracts.execution import Finding, SolveOutcome

from . import __version__
from .configuration import CASE, MAX_CIF_BYTES, MAX_SITES, TOOL, configuration_from_lane

CONTRACT_VERSION = "1"
ROLE = "crystal.operator"
SKILL = "crystal-analyze"
KNOWLEDGE = "crystal.scope"
ARTIFACTS = {
    "polyhedra_json": "application/json",
    "polyhedra_table": "application/json",
    "polyhedra_csv": "text/csv",
    "types_csv": "text/csv",
    "sites_csv": "text/csv",
    "bonds_csv": "text/csv",
}

MANIFEST = {
    "id": "crystal",
    "distribution": "crystal-genui",
    "version": __version__,
    "title": "Crystal polyhedra",
    "description": "Analyze crystal coordination from supplied CIF files or bundled examples.",
    "capabilities": [CASE],
    "instructions": (
        "Use a bundled example, pasted CIF or uploaded CIF in crystal.source. An upload takes "
        "precedence over crystal.structure. Select the neighbor method before running. Show the "
        "analysis summary, polyhedron-types table, source and method limitations. Preserve units "
        "and null metrics. Do not claim examples are DFT-relaxed or infer experimental stability. "
        "The receipt checker does not independently validate the science. The interactive 3D "
        "viewer is available through the standalone MCP App or CLI HTML export."
    ),
    "promptKnowledge": [KNOWLEDGE],
    "roles": [
        {
            "id": ROLE,
            "label": "Crystal researcher",
            "autonomy": "run_solvers",
            "goal": "Inspect supplied crystal coordination and explain the method's limitations",
            "skills": [SKILL],
        }
    ],
    "tools": [TOOL],
    "skills": [
        {
            "id": SKILL,
            "description": "Inspect crystal coordination metrics and provenance",
            "path": "skills/analyze-crystal/SKILL.md",
        }
    ],
    "knowledge": [
        {
            "id": KNOWLEDGE,
            "title": "Crystal analysis scope and units",
            "path": "knowledge/scope.md",
            "mediaType": "text/markdown",
        }
    ],
    "mcpServers": [{"id": "solver-crystal", "tools": [TOOL]}],
    "schemaRefs": {TOOL: f"mcp://solver-crystal/tools/{TOOL}"},
    "packageDependencies": [
        "consilith-contracts",
        "pymatgen>=2024.6",
        "scipy",
        "numpy",
        "pydantic>=2",
        "mcp>=2.3,<3",
        "uvicorn>=0.54,<1",
    ],
    "artifactMediaTypes": ["chemical/x-cif", "text/plain", "text/csv", "application/json"],
    "policyPorts": ["snapshot", "artifact", "run_tool", "review", "result"],
    "replayPolicy": {"relativeTolerance": 0, "absoluteTolerance": 0},
    "dependencies": [],
}


async def workflow(ctx, *, review=True, **options):
    model, snapshot = ctx.snapshot(("crystal",))
    try:
        lane = model.get("crystal", {})
        cfg = configuration_from_lane(lane)
        source = lane.get("source")
        encoded = ""
        structure = lane.get("structure", "SrTiO3")
        if source:
            if not isinstance(source, dict):
                raise ValueError("Upload a CIF belonging to this room")
            sha = source.get("artifact") or source.get("sha256")
            raw = ctx.artifact(sha) if isinstance(sha, str) else None
            if raw is None or hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError("Upload a CIF belonging to this room")
            if len(raw) > MAX_CIF_BYTES:
                raise ValueError("CIF exceeds the 1 MiB input limit")
            raw.decode("utf-8")
            encoded, structure = base64.b64encode(raw).decode(), ""
        if not isinstance(structure, str):
            raise ValueError("Structure must be an example name or CIF text")
        arguments = {
            "structure": structure,
            "cif_base64": encoded,
            "configuration": cfg.model_dump(),
        }
    except (ValueError, KeyError, TypeError) as exc:
        return SolveOutcome(error=str(exc))
    run_id, _envelope, records, error = await ctx.run_tool(TOOL, arguments, 2, snapshot=snapshot)
    outcome = SolveOutcome(runs=[run_id], results={r.quantity: r for r in records}, error=error)
    if records and review:
        outcome = await ctx.review(records, outcome)
    outcome.stamps = {record.quantity: ctx.result(record.id).stamp.value for record in records}
    return outcome


async def checker(request, item, call):
    """Check receipt binding and typed outputs without importing/replaying the science."""
    args = request.manifest.arguments
    passed = request.manifest.tool == TOOL and request.case == CASE and item.unit == ""
    value = item.value
    if item.quantity == "analysis_receipt":
        passed = passed and item.kind == "json" and isinstance(value, dict)
        if passed:
            count = value.get("siteCount")
            passed = (
                value.get("inputDigest") == digest(args)
                and value.get("experimentalValidation") == "not_performed"
                and isinstance(count, int)
                and not isinstance(count, bool)
                and 1 <= count <= MAX_SITES
            )
    elif item.quantity == "polyhedra_report":
        passed = (
            passed
            and item.kind == "json"
            and isinstance(value, dict)
            and value.get("schema") == "crystal-genui/polyhedra@1"
            and isinstance(value.get("polyhedra"), list)
            and isinstance(value.get("structure"), dict)
        )
    elif item.quantity == "analysis_summary":
        passed = passed and item.kind == "text" and isinstance(value, str) and bool(value)
    elif item.quantity in ARTIFACTS:
        passed = (
            passed
            and item.kind == "artifact"
            and bool(item.artifacts)
            and all(artifact.media_type == ARTIFACTS[item.quantity] for artifact in item.artifacts)
        )
    else:
        passed = False
    return [
        Finding(
            Check(
                name="crystal-receipt",
                outcome="pass" if passed else "fail",
                detail="Checked receipt input binding and supported output kinds; no replay.",
            ),
            "block",
        ),
        Finding(
            Check(
                name="experimental-validation",
                outcome="warn",
                detail="Coordination diagnostics do not establish experimental validity.",
            ),
            "warn",
        ),
    ], "bounded crystal receipt review; scientific validity unverified"


def role():
    return AgentConfig(
        autonomy=Autonomy.RUN_SOLVERS,
        tools_allowed=[TOOL],
        skills=[SKILL],
        compute_budget_s=600,
        goal="Analyze supplied crystals with explicit source, units and coordination assumptions",
    )


def numeric(name, label, default, minimum, maximum):
    return FieldDef(
        name=name,
        label=label,
        kind="number",
        min=minimum,
        max=maximum,
        default=None if default is None else {"v": default, "unit": ""},
    )


def extension():
    case = CaseDef(
        id=CASE,
        label="Crystal polyhedra",
        grammar="Supply a crystal, choose coordination settings, then inspect the computed report.",
        inputs=["crystal-cif"],
        lanes=[
            LaneDef(
                id="crystal",
                label="Crystal and coordination",
                default_role=ROLE,
                fields=[
                    FieldDef(
                        name="structure",
                        label="Example name or CIF text",
                        kind="text",
                        default="SrTiO3",
                    ),
                    FieldDef(
                        name="source",
                        label="Upload CIF (overrides text while present)",
                        kind="object",
                    ),
                    FieldDef(
                        name="method",
                        label="Coordination method",
                        kind="choice",
                        choices=["shannon", "crystalnn", "cutoff"],
                        default="shannon",
                    ),
                    numeric(
                        "max_bond", "Maximum bond distance (angstrom; cutoff only)", None, 0.001, 6
                    ),
                    numeric("min_bond", "Minimum bond distance (angstrom)", 0, 0, 5.999),
                    numeric(
                        "radius_tolerance", "Shannon radius tolerance (fraction)", 0.15, 0, 0.5
                    ),
                    FieldDef(
                        name="include_element_shells",
                        label="Include nearest element shells",
                        kind="choice",
                        choices=["no", "yes"],
                        default="no",
                    ),
                ],
            )
        ],
    )
    return {
        "plugin": {"manifest": MANIFEST},
        "cases": {CASE: case},
        "roles": {ROLE: role},
        "workflows": {CASE: workflow},
        "checkers": {CASE: checker},
        "workflow_required_tools": {CASE: [TOOL]},
        "default_modes": {CASE: "analyze"},
        "queue_modes": {CASE: ["analyze"]},
        "presentation": {
            CASE: {
                "subtitle": "Supply crystal → analyze coordination → inspect metrics",
                "intakeBindings": {
                    "crystal-cif": {
                        "lane": "crystal",
                        "field": "source",
                        "valueKind": "artifact",
                        "mediaType": "chemical/x-cif",
                    }
                },
            }
        },
        "intake_hints": {CASE: ["crystal", "polyhedra", "CIF", "coordination", "perovskite"]},
        "intake_kinds": {"crystal-cif": CASE},
    }
