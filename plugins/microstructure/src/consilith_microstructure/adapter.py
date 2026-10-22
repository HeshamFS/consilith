"""Optional platform extension; scientific code executes only in the plugin MCP service."""

import base64
import hashlib
from itertools import pairwise

from consilith_contracts import AgentConfig, Autonomy, CaseDef, Check, FieldDef, LaneDef
from consilith_contracts.base import digest
from consilith_contracts.execution import Finding, SolveOutcome

from . import __version__

CONTRACT_VERSION = "1"
REPORT_TOOL = "microstructure.report"
DATASET_TOOL = "microstructure.dataset"
ESTIMATE_TOOL = "adaption.estimate"
ADAPT_TOOL = "adaption.adapt"
# The first two stay at indexes 0 and 1: the showcase workflow and checker read them by index.
TOOLS = [
    "microstructure.prepare_showcase",
    "microstructure.evolve_showcase",
    REPORT_TOOL,
    DATASET_TOOL,
    ESTIMATE_TOOL,
    ADAPT_TOOL,
]
SERVERS = {
    "solver-microstructure": TOOLS[:4],
    "solver-adaption": TOOLS[4:],
}
REPORT_MODE = "report"
DATASET_MODE = "dataset"
# The mode id is what the confirm card shows, so it says that credits are spent.
ADAPT_MODE = "adapt-spends-credits"
# Fetch the result of an adaptation that was still running; spends nothing.
ADAPT_RESULT_MODE = "adapt-result"
MODE_ALIASES = {"adapt": ADAPT_MODE}
DERIVED_MODES = {REPORT_MODE, DATASET_MODE, ADAPT_MODE, ADAPT_RESULT_MODE, *MODE_ALIASES}
PARAMETER_KEYS = ("duration", "gamma", "mobility", "width")
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
    "growth": "auto",
}

MANIFEST = {
    "id": "microstructure",
    "distribution": "consilith-microstructure",
    "version": __version__,
    "title": "Microstructure grain growth",
    "description": (
        "Upload a micrograph, review candidate grain boundaries, "
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
        "dimensionless; segmentation is not validated against ground truth. No SAM is used. "
        "After an evolution, offer three follow-up modes of the same capability. "
        "Mode 'report' writes a marimo HTML report of the run. Mode 'dataset' builds a small "
        "question-and-answer training set from the run's own tables and asks Adaption Labs "
        "for a credit estimate; nothing is spent. Mode 'adapt-spends-credits' spends those "
        "credits: run it only after the dataset step has shown the estimate, and only when the "
        "person asks for it. Never run adapt before the estimate. Adaption can take many "
        "minutes: if the adapt step reports the run as still going, run mode 'adapt-result' "
        "later to fetch the evaluation and the adapted rows (it spends nothing). The person "
        "sets the training type, recipes, answer length and blueprint in the Training data "
        "fields. The report is presentation; "
        "the dataset is derived and illustrative; the Adaption score rates the text, not the "
        "physics. Say so."
    ),
    "promptKnowledge": ["microstructure.showcase"],
    "roles": [
        {
            "id": "microstructure.operator",
            "label": "Microstructure researcher",
            "autonomy": "run_solvers",
            "goal": (
                "Review an uploaded grain map, explore a hypothetical 2D evolution, then offer "
                "a report, a training set with a credit estimate, and only on request the adapt"
            ),
            "skills": ["micrograph-grain-growth"],
        }
    ],
    "tools": TOOLS,
    "skills": [
        {
            "id": "micrograph-grain-growth",
            "description": "Upload, human segmentation review, and bounded phase-field evolution",
            "path": "skills/micrograph-grain-growth/SKILL.md",
        }
    ],
    "knowledge": [
        {
            "id": "microstructure.showcase",
            "title": "Micrograph grain-growth limits",
            "path": "knowledge/showcase.md",
            "mediaType": "text/markdown",
        }
    ],
    "mcpServers": [{"id": server, "tools": tools} for server, tools in SERVERS.items()],
    "schemaRefs": {
        name: f"mcp://{server}/tools/{name}" for server, tools in SERVERS.items() for name in tools
    },
    "packageDependencies": [
        "consilith-contracts",
        "numpy==2.4.2",
        "scipy==1.17.0",
        "scikit-image==0.26.0",
        "Pillow==12.1.1",
        "numba==0.65.1",
        "mcp==2.3.0",
        "uvicorn==0.54.0",
        "marimo==0.25.2",
        "adaption==0.15.0",
    ],
    "artifactMediaTypes": [
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/gif",
        "application/json",
        "application/x-npz",
        "text/html",
        "application/jsonl",
    ],
    "policyPorts": ["snapshot", "artifact", "current_results", "result", "run_tool", "review"],
    "replayPolicy": {"relativeTolerance": 0, "absoluteTolerance": 0},
    "dependencies": [],
}


DERIVED_CHECKS = {REPORT_TOOL, DATASET_TOOL, ESTIMATE_TOOL, ADAPT_TOOL}


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
    for key in ("duration", "gamma", "mobility", "growth"):
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


def run_parameters(report):
    executed = report.get("executedConfiguration", {})
    return {key: executed[key] for key in PARAMETER_KEYS if key in executed}


def evolution_arguments(results):
    """The trajectory block and parameters of the latest evolution result."""
    record = results.get("evolution_report")
    value = getattr(record, "value", None)
    if (
        record is None
        or record.provenance.tool != TOOLS[1]
        or record.stamp.value == "blocked"
        or not isinstance(value, dict)
        or not isinstance(value.get("trajectory"), dict)
    ):
        raise ValueError("Run the evolution first: this step reads its evolution_report result")
    return {"trajectory": value["trajectory"], "parameters": run_parameters(value)}


def latest_estimate(results):
    """The current Adaption estimate, if it is still the one for the latest training set."""
    record = results.get("adaption_estimate")
    value = getattr(record, "value", None)
    if record is None or not isinstance(value, dict):
        raise ValueError(
            "Run the dataset step first: it uploads the set and shows the credit estimate"
        )
    summary = results.get("training_set_summary")
    stale = summary is not None and (summary.value or {}).get("sha256") != value.get("uploadSha256")
    if stale or not value.get("withinLimit") or not isinstance(value.get("datasetId"), str):
        raise ValueError(
            "The latest estimate is over the credit limit or does not match the latest "
            "training set; run the dataset step again"
        )
    return value


async def finish(ctx, outcome, records, review):
    if records and review:
        outcome = await ctx.review(records, outcome)
    outcome.stamps = {r.quantity: ctx.result(r.id).stamp.value for r in records}
    return outcome


TRAINING_KEYS = ("training_type", "recipes", "answer_length", "blueprint")


def report_references(model):
    """The report's related-work lines from the evolution lane, blank lines dropped."""
    raw = model.get("evolution", {}).get("report_references") or ""
    return [line.strip() for line in str(raw).splitlines() if line.strip()][:8]


def training_options(model):
    """Adaption's configuration step, from the person's Training data fields."""
    evolution = model.get("evolution", {})
    options = {}
    for key in TRAINING_KEYS:
        if key in evolution and evolution[key] not in (None, ""):
            options["length" if key == "answer_length" else key] = number(evolution[key])
    return options


async def derived_workflow(ctx, mode, review):
    """Report, training-set and Adaption steps, on the latest evolution of this capability."""
    model, snapshot = ctx.snapshot(("image", "evolution"))
    results = ctx.current_results()
    try:
        if mode == ADAPT_RESULT_MODE:
            estimate = latest_estimate(results)
            steps = [
                (
                    ADAPT_TOOL,
                    {
                        "dataset_id": estimate["datasetId"],
                        "confirmed": True,
                        "options": estimate.get("options") or {},
                        "resume": True,
                    },
                    {"estimate_s": 300.0},
                )
            ]
        elif mode == ADAPT_MODE:
            estimate = latest_estimate(results)
            credits = estimate["estimatedCredits"]
            steps = [
                (
                    ADAPT_TOOL,
                    {
                        "dataset_id": estimate["datasetId"],
                        "confirmed": True,
                        "expected_credits": credits,
                        # The options the person was quoted for, never different ones.
                        "options": estimate.get("options") or {},
                    },
                    {
                        "estimate_s": 600.0,
                        "context": {
                            "description": (
                                f"Spends about {credits:g} Adaption credits to adapt "
                                f"{estimate['rows']} rows and evaluate them"
                            ),
                            "creditsSpent": True,
                            "estimatedCredits": credits,
                            "rows": estimate["rows"],
                        },
                    },
                )
            ]
        else:
            tool = REPORT_TOOL if mode == REPORT_MODE else DATASET_TOOL
            arguments = evolution_arguments(results)
            if mode == REPORT_MODE:
                arguments = {**arguments, "references": report_references(model)}
            steps = [(tool, arguments, {"estimate_s": 60.0})]
    except (ValueError, KeyError, TypeError) as exc:
        return SolveOutcome(error=str(exc))
    runs, records, error = [], [], None
    for tool, arguments, options in steps:
        run_id, _envelope, made, error = await ctx.run_tool(
            tool, arguments, 2, snapshot=snapshot, **options
        )
        runs.append(run_id)
        records += made
    if not error and mode == DATASET_MODE:
        summary = next((r for r in records if r.quantity == "training_set_summary"), None)
        digest_ = ((summary.value if summary else None) or {}).get("sha256", "")
        run_id, _envelope, made, error = await ctx.run_tool(
            ESTIMATE_TOOL,
            {**steps[0][1], "expected_sha256": digest_, "options": training_options(model)},
            2,
            snapshot=snapshot,
            estimate_s=120.0,
        )
        runs.append(run_id)
        records += made
    outcome = SolveOutcome(runs=runs, results={r.quantity: r for r in records}, error=error)
    return await finish(ctx, outcome, records, review)


async def workflow(ctx, *, review=True, mode=None, **options):
    mode = MODE_ALIASES.get(mode, mode)
    if mode in DERIVED_MODES:
        return await derived_workflow(ctx, mode, review)
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
                        key: config[key] for key in ("duration", "gamma", "mobility", "growth")
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


def media(item):
    return [(a.media_type, a.role) for a in getattr(item, "artifacts", [])]


def table_shaped(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("columns"), list)
        and isinstance(value.get("rows"), list)
    )


def whole(value):
    """A count as the referee sees it: JSON numbers can arrive as floats (200.0)."""
    return (
        isinstance(value, int | float) and not isinstance(value, bool) and float(value).is_integer()
    )


def derived_findings(tool, item):
    """Receipt checks for the report, training set and Adaption outputs, each with the honest
    warning for what it is. None of them checks the physics."""
    value, quantity = item.value, item.quantity
    if tool == REPORT_TOOL:
        passed = quantity == "microstructure_report" and media(item) == [("text/html", "report")]
        warning = (
            "report-is-presentation",
            "The report presents the run's computed tables. It is not a check of the physics.",
        )
    elif tool == DATASET_TOOL:
        if quantity == "training_set":
            passed = media(item) == [("application/jsonl", "dataset")]
        else:
            passed = (
                quantity == "training_set_summary"
                and isinstance(value, dict)
                and whole(value.get("rows"))
                and 40 <= value["rows"] <= 200
                and value.get("columns") == ["prompt", "completion"]
            )
        warning = (
            "dataset-is-derived",
            "Rows are derived from the run's own tables and are illustrative text about a "
            "hypothetical dimensionless model, not data about a material.",
        )
    elif tool == ESTIMATE_TOOL:
        passed = (
            quantity == "adaption_estimate"
            and isinstance(value, dict)
            and isinstance(value.get("datasetId"), str)
            and isinstance(value.get("estimatedCredits"), int | float)
            and value.get("nothingSpent") is True
        )
        warning = (
            "estimate-only",
            "A credit quote from Adaption Labs. Nothing was spent; the adapt step spends credits.",
        )
    else:
        if quantity == "adaption_evaluation":
            passed = isinstance(value, dict) and isinstance(value.get("skipped"), bool)
        elif quantity == "adaption_evaluation_table":
            passed = table_shaped(value)
        elif quantity == "adaption_adapted_dataset":
            passed = media(item) == [("application/jsonl", "dataset")]
        else:
            passed = quantity == "adaption_spend" and isinstance(value, dict)
        warning = (
            "evaluation-is-text-quality",
            "Adaption's evaluation is the vendor's score of the text quality of the rows. It is "
            "not a check of the physics.",
        )
    return [
        Finding(
            Check(
                name="derived-receipt",
                outcome="pass" if passed else "fail",
                detail="Checked the output name, kind, media type and declared shape.",
            ),
            "block",
        ),
        Finding(Check(name=warning[0], outcome="warn", detail=warning[1]), "warn"),
    ], "bounded receipt review; derived output, not independent validation"


async def checker(request, item, call):
    """Bounded receipt checks, explicitly not independent physics validation."""
    tool = request.manifest.tool
    args = request.manifest.arguments
    if tool in DERIVED_CHECKS:
        return derived_findings(tool, item)
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
        skills=["micrograph-grain-growth"],
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
        label="Microstructure grain growth",
        grammar=(
            "Upload a micrograph with visible grain boundaries. The entire native-resolution "
            "image is used. Run prepares "
            "segmentation and transfer checks. Review the boundary overlay, approve "
            "the result, then Run again to compute the full dimensionless 2D evolution."
        ),
        inputs=["micrograph"],
        lanes=[
            LaneDef(
                id="image",
                label="Image and segmentation",
                default_role="microstructure.operator",
                fields=[
                    FieldDef(
                        name="source",
                        label="Upload a micrograph (PNG, JPEG or TIFF)",
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
                    FieldDef(
                        name="growth",
                        label="Growth extent",
                        kind="choice",
                        choices=["auto", "fixed"],
                        default="auto",
                    ),
                    numeric(
                        "duration",
                        "Evolution duration, fixed extent only (dimensionless)",
                        600,
                        0.001,
                        10000,
                    ),
                    numeric("gamma", "Interface energy (dimensionless)", 1, 0.00001, 100),
                    numeric("mobility", "Boundary mobility (dimensionless)", 1, 0.00001, 100),
                    # Adaption's configuration step for the training set built from this run.
                    FieldDef(
                        name="training_type",
                        label="Training data: what to build",
                        kind="choice",
                        choices=["instruction_dataset", "preference_pairs"],
                        default="instruction_dataset",
                    ),
                    FieldDef(
                        name="recipes",
                        label="Training data: recipes",
                        kind="multichoice",
                        choices=["deduplication", "prompt_rephrase", "reasoning_traces"],
                        default=["deduplication", "reasoning_traces"],
                    ),
                    FieldDef(
                        name="answer_length",
                        label="Training data: answer length",
                        kind="choice",
                        choices=["minimal", "concise", "detailed", "extensive"],
                        default="concise",
                    ),
                    FieldDef(
                        name="blueprint",
                        label="Training data: blueprint (system instructions for the answers)",
                        kind="text",
                        multiline=True,
                        default=(
                            "You are a careful materials-science tutor. Answer from the "
                            "simulation's own tables, state every number with its unit, and say "
                            "that the model is hypothetical and dimensionless; never claim "
                            "experimental validation."
                        ),
                    ),
                    # Related work for the report, one per line (an agent can fill it from
                    # Valency's paper search): title, authors, year and a link at the end.
                    FieldDef(
                        name="report_references",
                        label="Report: related work, one per line (title, authors, year, link)",
                        kind="text",
                        multiline=True,
                        default="",
                    ),
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
        "queue_modes": {
            case.id: ["showcase", REPORT_MODE, DATASET_MODE, ADAPT_MODE, ADAPT_RESULT_MODE]
        },
        "presentation": {
            case.id: {
                "subtitle": "Upload → review boundaries → watch actual computed evolution",
                "intakeBindings": {
                    "micrograph": {
                        "label": "Micrograph",
                        "lane": "image",
                        "field": "source",
                        "valueKind": "artifact",
                        "mediaType": "image/*",
                    }
                },
            }
        },
        "intake_hints": {case.id: ["grain", "micrograph", "microstructure", "grain growth"]},
        "intake_kinds": {"micrograph": case.id},
    }
