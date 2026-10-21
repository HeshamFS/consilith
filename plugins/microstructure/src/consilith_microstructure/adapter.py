"""Optional platform extension; scientific code executes only in the plugin MCP service."""

import base64
import hashlib
from itertools import pairwise

from consilith_contracts import AgentConfig, Autonomy, CaseDef, Check, FieldDef, LaneDef
from consilith_contracts.base import digest
from consilith_contracts.execution import Finding, SolveOutcome

from . import __version__

CONTRACT_VERSION = "1"
TOOLS = ["microstructure.prepare_showcase", "microstructure.evolve_showcase"]
DEFAULT_CONFIGURATION = {
    "crop": [],
    "annotation_policy": "detect-edge-strip",
    "footer_rows": 0,
    "method": "global",
    "boundary": "dark",
    "threshold": None,
    "minimum_region": 12,
    "width": 4.0,
    "duration": 600.0,
    "gamma": 1.0,
    "mobility": 1.0,
}

MANIFEST = {
    "id": "microstructure",
    "distribution": "consilith-microstructure",
    "version": __version__,
    "title": "Clear-grain evolution",
    "description": (
        "Upload a clear-grain image, review candidate boundaries, "
        "then watch computed 2D grain growth."
    ),
    "capabilities": ["grain-growth"],
    "instructions": (
        "Upload an image into image.source. Segment and evolve the entire image at native "
        "resolution. Never crop or resample it. "
        "Prepare first, show segmentation_preview, material_domain, resolution_notice and "
        "initialization diagnostics. Annotation strips are non-material proposals requiring "
        "review; retain their original pixels on the full canvas. If automatic annotation "
        "detection is uncertain, request a non-material footer height and review its overlay. "
        "Never bypass the block by silently choosing 'none'. Wait for "
        "the human to approve the displayed segmentation through reviewed_result. Never set "
        "that human-only field. Then run evolution. Show the full computed image sequence and "
        "energy history. Never invent grain values. All parameters are hypothetical and "
        "dimensionless; segmentation is not validated against ground truth. No SAM is used."
    ),
    "promptKnowledge": ["microstructure.showcase"],
    "roles": [
        {
            "id": "microstructure.operator",
            "label": "Microstructure researcher",
            "autonomy": "run_solvers",
            "goal": "Review an uploaded grain map and explore a hypothetical 2D evolution",
            "skills": ["clear-grain-showcase"],
        }
    ],
    "tools": TOOLS,
    "skills": [
        {
            "id": "clear-grain-showcase",
            "description": "Upload, human segmentation review, and bounded phase-field evolution",
            "path": "skills/clear-grain-showcase/SKILL.md",
        }
    ],
    "knowledge": [
        {
            "id": "microstructure.showcase",
            "title": "Clear-grain showcase limits",
            "path": "knowledge/showcase.md",
            "mediaType": "text/markdown",
        }
    ],
    "mcpServers": [{"id": "solver-microstructure", "tools": TOOLS}],
    "schemaRefs": {name: f"mcp://solver-microstructure/tools/{name}" for name in TOOLS},
    "packageDependencies": [
        "consilith-contracts",
        "numpy==2.4.2",
        "scipy==1.17.0",
        "scikit-image==0.26.0",
        "Pillow==12.1.1",
        "numba==0.65.1",
        "mcp==2.3.0",
        "uvicorn==0.54.0",
    ],
    "artifactMediaTypes": [
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/gif",
        "application/json",
        "application/x-npz",
    ],
    "policyPorts": ["snapshot", "artifact", "current_results", "result", "run_tool", "review"],
    "replayPolicy": {"relativeTolerance": 0, "absoluteTolerance": 0},
    "dependencies": [],
}


def number(value):
    return value.get("v") if isinstance(value, dict) else value


def configuration(model):
    image, evolution = model.get("image", {}), model.get("evolution", {})
    config = dict(DEFAULT_CONFIGURATION)
    for key in (
        "crop",
        "annotation_policy",
        "footer_rows",
        "method",
        "boundary",
        "threshold",
        "minimum_region",
        "width",
    ):
        if key in image:
            config[key] = number(image[key])
    for key in ("duration", "gamma", "mobility"):
        if key in evolution:
            config[key] = number(evolution[key])
    config["minimum_region"] = int(config["minimum_region"])
    if config["footer_rows"] != int(config["footer_rows"]):
        raise ValueError("Footer rows must be an integer")
    config["footer_rows"] = int(config["footer_rows"])
    # Canonical numeric representation matches the service's validated configuration.
    for key in ("width", "duration", "gamma", "mobility"):
        config[key] = float(config[key])
    if config["threshold"] is not None:
        config["threshold"] = float(config["threshold"])
    return config


async def workflow(ctx, *, review=True, **options):
    model, snapshot = ctx.snapshot(("image", "evolution"))
    try:
        source = model["image"].get("source", {})
        source_sha = source.get("artifact") or source.get("sha256")
        raw = ctx.artifact(source_sha) if isinstance(source_sha, str) else None
        if raw is None or hashlib.sha256(raw).hexdigest() != source_sha:
            raise ValueError("Upload an image belonging to this room before running the showcase")
        config = configuration(model)
        expected = digest({"sourceSha256": source_sha, "configuration": config})
        selected = model.get("evolution", {}).get("reviewed_result", "")
        if selected:
            record = ctx.result(selected)
            payload = record.value
            if (
                record.case != "grain-growth"
                or record.quantity != "segmentation_review"
                or record.kind != "json"
                or not isinstance(payload, dict)
            ):
                raise ValueError("Choose a segmentation review result from this capability")
            reviewed_config = payload.get("configuration", {})
            expected = digest({"sourceSha256": source_sha, "configuration": reviewed_config})

            def segmentation_config(values):
                return {
                    key: value
                    for key, value in values.items()
                    if key not in {"duration", "gamma", "mobility"}
                }

            if (
                record.provenance.tool != TOOLS[0]
                or payload.get("fingerprint") != expected
                or segmentation_config(reviewed_config) != segmentation_config(config)
            ):
                raise ValueError(
                    "Image or segmentation parameters changed since review. "
                    "Clear approval, segment again and review the new preview."
                )
            if record.stamp.value == "blocked" or payload.get("readyForEvolution") is not True:
                raise ValueError(
                    "This segmentation failed its transfer gates; "
                    "correct the image or parameters first"
                )
            tool, arguments = (
                TOOLS[1],
                {
                    "review": payload,
                    "approved_fingerprint": expected,
                    "evolution_parameters": {
                        key: config[key] for key in ("duration", "gamma", "mobility")
                    },
                    "image_base64": base64.b64encode(raw).decode(),
                },
            )
        else:
            tool, arguments = (
                TOOLS[0],
                {"image_base64": base64.b64encode(raw).decode(), "configuration": config},
            )
    except (ValueError, KeyError, TypeError) as exc:
        return SolveOutcome(error=str(exc))
    run_id, _envelope, records, error = await ctx.run_tool(tool, arguments, 2, snapshot=snapshot)
    outcome = SolveOutcome(runs=[run_id], results={r.quantity: r for r in records}, error=error)
    if records and review:
        outcome = await ctx.review(records, outcome)
    outcome.stamps = {record.quantity: ctx.result(record.id).stamp.value for record in records}
    return outcome


async def checker(request, item, call):
    """Bounded receipt checks, explicitly not independent physics validation."""
    tool = request.manifest.tool
    args = request.manifest.arguments
    passed = item.kind in {"json", "artifact", "text"} and item.unit == ""
    allowed = (
        {
            "segmentation_review",
            "resolution_notice",
            "segmentation_preview",
            "uploaded_image",
            "material_domain",
            "grain_labels",
            "grain_map",
        }
        if tool == TOOLS[0]
        else {
            "evolution_report",
            "evolution_sequence",
            "evolution_animation",
            "evolution_checkpoint",
            "evolution_history",
            "grain_area_history",
            "free_energy_curve",
            "shrinking_grain_curve",
            "evolution_frames_1",
            "evolution_frames_2",
            "evolution_frames_3",
            "evolution_frames_4",
        }
        if tool == TOOLS[1]
        else set()
    )
    passed = passed and item.quantity in allowed
    if item.quantity == "segmentation_review":
        value = item.value
        source = base64.b64decode(args["image_base64"], validate=True)
        expected = digest(
            {
                "sourceSha256": hashlib.sha256(source).hexdigest(),
                "configuration": {**DEFAULT_CONFIGURATION, **args["configuration"]},
            }
        )
        passed = passed and isinstance(value, dict) and value.get("fingerprint") == expected
        if passed:
            passed = (
                value.get("physicalValidation") == "not_performed"
                and value.get("segmentationAccuracy") == "not_measured"
            )
    elif item.quantity == "evolution_report":
        value = item.value
        passed = (
            passed
            and isinstance(value, dict)
            and value.get("reviewedFingerprint") == args["approved_fingerprint"]
            and value.get("executedConfiguration")
            == {**args["review"]["configuration"], **(args.get("evolution_parameters") or {})}
        )
        if passed:
            history = value.get("trajectory", {})
            times, energies = history.get("times", []), history.get("energy", [])
            passed = (
                history.get("interpolatedFrames") is False
                and 2 <= len(times) <= 61
                and len(times) == len(energies)
                and all(b >= a for a, b in pairwise(times))
                and all(b <= a + 1e-8 * max(1, abs(a)) for a, b in pairwise(energies))
            )
    elif item.quantity == "resolution_notice":
        passed = passed and item.kind == "text" and isinstance(item.value, str)
    else:
        passed = passed and item.kind == "artifact" and bool(item.artifacts)
    return [
        Finding(
            Check(
                name="showcase-receipt",
                outcome="pass" if passed else "fail",
                detail=(
                    "Checked typed output names, receipt binding and declared "
                    "trajectory diagnostics; no scientific replay."
                ),
            ),
            "block",
        ),
        Finding(
            Check(
                name="experimental-validation",
                outcome="warn",
                detail=(
                    "Hypothetical dimensionless showcase. Segmentation ground truth "
                    "and experimental kinetics have not been validated."
                ),
            ),
            "warn",
        ),
    ], "bounded showcase receipt review; scientific validity unverified"


def role():
    return AgentConfig(
        autonomy=Autonomy.RUN_SOLVERS,
        tools_allowed=TOOLS,
        skills=["clear-grain-showcase"],
        compute_budget_s=600,
        goal="Review the supplied micrograph before exploring hypothetical grain growth",
    )


def numeric(name, label, default, minimum, maximum, *, integer=False):
    return FieldDef(
        name=name,
        label=label,
        kind="integer" if integer else "number",
        min=minimum,
        max=maximum,
        default=None if default is None else {"v": default, "unit": ""},
    )


def extension():
    case = CaseDef(
        id="grain-growth",
        label="Clear-grain evolution",
        grammar=(
            "Upload a clear-grain image. The entire native-resolution image is used. Run prepares "
            "segmentation and transfer checks. Review the boundary overlay, approve "
            "the result, then Run again to compute the full dimensionless 2D evolution."
        ),
        inputs=["clear-grain-image"],
        lanes=[
            LaneDef(
                id="image",
                label="Image and segmentation",
                default_role="microstructure.operator",
                fields=[
                    FieldDef(
                        name="source",
                        label="Upload clear-grain image (PNG, JPEG or TIFF)",
                        kind="object",
                        required=True,
                    ),
                    FieldDef(
                        name="annotation_policy",
                        label="Annotation handling (review the material-domain mask)",
                        kind="choice",
                        choices=["detect-edge-strip", "none"],
                        default="detect-edge-strip",
                    ),
                    numeric(
                        "footer_rows",
                        "Non-material footer height (pixels; 0 = automatic)",
                        0,
                        0,
                        4194304,
                        integer=True,
                    ),
                    FieldDef(
                        name="method",
                        label="Boundary segmentation",
                        kind="choice",
                        choices=["global", "adaptive", "ridge"],
                        default="global",
                    ),
                    FieldDef(
                        name="boundary",
                        label="Grain boundary contrast",
                        kind="choice",
                        choices=["dark", "bright"],
                        default="dark",
                    ),
                    numeric(
                        "threshold",
                        "Optional normalized threshold (omit for automatic)",
                        None,
                        0.00001,
                        0.99999,
                    ),
                    numeric(
                        "minimum_region",
                        "Minimum grain seed area (pixels)",
                        12,
                        1,
                        4096,
                        integer=True,
                    ),
                    numeric("width", "Diffuse interface width (pixels)", 4, 4, 32),
                ],
            ),
            LaneDef(
                id="evolution",
                label="Review and evolution",
                default_role="microstructure.operator",
                fields=[
                    numeric("duration", "Evolution duration (dimensionless)", 600, 0.001, 10000),
                    numeric("gamma", "Interface energy (dimensionless)", 1, 0.00001, 100),
                    numeric("mobility", "Boundary mobility (dimensionless)", 1, 0.00001, 100),
                    FieldDef(
                        name="reviewed_result",
                        label="Approve displayed segmentation and parameters",
                        kind="text",
                        default="",
                        human_only=True,
                        review_quantity="segmentation_review",
                    ),
                ],
            ),
        ],
    )
    return {
        "plugin": {"manifest": MANIFEST},
        "cases": {case.id: case},
        "roles": {"microstructure.operator": role},
        "workflows": {case.id: workflow},
        "checkers": {case.id: checker},
        "workflow_required_tools": {case.id: TOOLS},
        "default_modes": {case.id: "showcase"},
        "queue_modes": {case.id: ["showcase"]},
        "presentation": {
            case.id: {
                "subtitle": "Upload → review boundaries → watch actual computed evolution",
                "intakeBindings": {
                    "clear-grain-image": {
                        "lane": "image",
                        "field": "source",
                        "valueKind": "artifact",
                        "mediaType": "image/*",
                    }
                },
            }
        },
        "intake_hints": {case.id: ["grain", "micrograph", "microstructure", "grain growth"]},
        "intake_kinds": {"clear-grain-image": case.id},
    }
