"""Optional platform envelopes around the independent, SAM-free showcase kernel.

No room, referee or UI imports. Inputs are supplied bytes and reviewed content, never
filesystem paths or URLs. Native image resolution is preserved throughout the workflow.
"""

import base64
import hashlib
import time
from io import BytesIO
from math import ceil
from typing import Any, Literal

import numpy as np
from consilith_contracts import ResultEnvelope, canonical_bytes
from consilith_contracts.base import digest
from consilith_contracts.solver import implementation_identity, inputs_hash
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from skimage.segmentation import find_boundaries

from . import __version__
from .annotation import detect_annotation_mask
from .data import MAX_PIXELS, GrainMap
from .pipeline import png_labels, reconstruct, run
from .solver import Parameters
from .trajectory import trajectory, trajectory_npz

WORK_BUDGET = 12_000_000_000
FRAME_COUNT = 61


class ShowcaseConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    crop: list[StrictInt] = Field(default_factory=list)
    annotation_policy: Literal["detect-edge-strip", "none"] = "detect-edge-strip"
    footer_rows: StrictInt = Field(default=0, ge=0, le=MAX_PIXELS)
    method: Literal["global", "adaptive", "ridge"] = "global"
    boundary: Literal["dark", "bright"] = "dark"
    threshold: float | None = Field(default=None, gt=0, lt=1)
    minimum_region: StrictInt = Field(default=12, ge=1, le=4096)
    width: float = Field(default=4.0, ge=4, le=32)
    duration: float = Field(default=600.0, gt=0, le=10000)
    gamma: float = Field(default=1.0, gt=0, le=100)
    mobility: float = Field(default=1.0, gt=0, le=100)


class EvolutionParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    duration: float = Field(gt=0, le=10000)
    gamma: float = Field(gt=0, le=100)
    mobility: float = Field(gt=0, le=100)


def fingerprint(source_sha256, configuration):
    return digest({"sourceSha256": source_sha256, "configuration": configuration})


def content(raw, media_type, role="output"):
    return {"mediaType": media_type, "contentBase64": base64.b64encode(raw).decode(), "role": role}


def file_output(name, raw, media_type, role="output"):
    return {"name": name, "kind": "artifact", "artifacts": [content(raw, media_type, role)]}


def envelope(tool, arguments, outputs, started, *, fields=None):
    return ResultEnvelope.model_validate(
        {
            "outputVersion": "1",
            "outputs": outputs,
            "provenance": {
                "tool": tool,
                "solverVersion": __version__,
                "inputsHash": inputs_hash(arguments),
                "wallTimeS": time.perf_counter() - started,
            },
            "validity": {
                "range": (
                    "Reviewed clear-boundary 2D images; hypothetical dimensionless "
                    "isotropic grain growth"
                ),
                "inside": True,
            },
            "tier": 2,
            "illustrative": True,
            "fields": fields or {},
            "notes": [
                "No SAM or learned segmentation model is used.",
                "Segmentation accuracy is not measured against ground truth; "
                "experimental kinetics are not validated.",
                "Native pixels, no resampling; no-flux boundaries; dimensionless parameters.",
            ],
        }
    ).wire()


def work_estimate(grains, cfg, *, enforce=False):
    p = Parameters(1.0, cfg.width, cfg.gamma, cfg.mobility)
    steps = ceil(cfg.duration / p.dt_limit)
    work = int(grains.labels.size * steps)
    if enforce and (work > WORK_BUDGET or steps > 20000):
        raise ValueError(
            "Requested duration exceeds the bounded native-grid work limit; "
            "reduce duration. The image will not be cropped or resampled."
        )
    return {
        "pixelSteps": work,
        "maximumPixelSteps": WORK_BUDGET,
        "estimatedSolverSteps": steps,
        "nativeShape": list(grains.labels.shape),
        "withinEvolutionBudget": work <= WORK_BUDGET and steps <= 20000,
    }


def png(image):
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def preview(raw, grains, crop):
    with Image.open(BytesIO(raw)) as source:
        source = source.convert("RGB")
        if crop:
            source = source.crop(tuple(crop))
    overlay = np.array(source)
    overlay[find_boundaries(grains.labels, mode="inner")] = (0, 220, 220)
    labels = Image.open(BytesIO(png_labels(grains.labels))).convert("RGB")
    if grains.domain_mask is not None:
        active = grains.domain_mask
        original = np.asarray(source)
        overlay[~active] = original[~active]
        label_pixels = np.asarray(labels).copy()
        label_pixels[~active] = original[~active]
        labels = Image.fromarray(label_pixels)
    scale = min(3, 420 / max(source.size))
    size = (round(source.width * scale), round(source.height * scale))
    canvas = Image.new("RGB", (3 * size[0] + 40, size[1] + 54), (17, 23, 32))
    draw = ImageDraw.Draw(canvas)
    for k, (im, title) in enumerate(
        (
            (source, "Full uploaded image"),
            (Image.fromarray(overlay), "Candidate boundaries"),
            (labels, "Grain identities"),
        )
    ):
        x = 10 + k * (size[0] + 10)
        canvas.paste(im.resize(size, Image.Resampling.NEAREST), (x, 30))
        draw.text((x, 10), title, fill="white")
    draw.text(
        (10, size[1] + 36),
        "Review the boundary correspondence before approving evolution.",
        fill="white",
    )
    return png(canvas), png(source)


def prepare_showcase(image_base64: str, configuration: dict[str, Any]) -> dict[str, Any]:
    """Segment a supplied clear-grain image; show source, boundary overlay and transfer gates."""
    started = time.perf_counter()
    if len(image_base64) > 14_000_000:
        raise ValueError("Encoded image exceeds showcase budget")
    raw = base64.b64decode(image_base64, validate=True)
    cfg = ShowcaseConfiguration.model_validate(configuration)
    config = cfg.model_dump()
    if cfg.crop:
        raise ValueError("The full-image workflow does not accept cropping; use crop=[]")
    with Image.open(BytesIO(raw)) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("Image exceeds the native-grid pixel budget; no automatic resizing")
    excluded, annotation = detect_annotation_mask(raw)
    if cfg.footer_rows:
        if cfg.annotation_policy == "none":
            raise ValueError("Footer rows cannot be combined with annotation handling 'none'")
        if cfg.footer_rows > excluded.shape[0] - 2:
            raise ValueError("Footer rows must leave at least two material rows")
        detected = annotation
        excluded = np.zeros_like(excluded)
        excluded[-cfg.footer_rows :] = True
        annotation = {
            **detected,
            "method": "explicit_footer_rows_v1",
            "status": "candidate_requires_review",
            "automatic_detection": detected,
            "excluded_pixels": int(excluded.sum()),
            "excluded_fraction": float(excluded.mean()),
            "mask_sha256": hashlib.sha256(excluded.tobytes()).hexdigest(),
            "candidate_bands": [
                {
                    "edge": "bottom",
                    "rows": [excluded.shape[0] - cfg.footer_rows, excluded.shape[0]],
                    "rows_convention": "zero_based_half_open",
                }
            ],
            "rejected_candidates": [],
            "unresolved_edges": [],
            "parameters": {"footer_rows": cfg.footer_rows},
            "limitation": "Explicit footer proposal; verify the overlay before approval.",
        }
    annotation_uncertain = (
        cfg.annotation_policy == "detect-edge-strip"
        and annotation["status"] == "annotation_review_required"
    )
    material_mask = (
        ~excluded if cfg.annotation_policy == "detect-edge-strip" and excluded.any() else None
    )
    _, grains = reconstruct(
        raw,
        crop=cfg.crop or None,
        method=cfg.method,
        boundary=cfg.boundary,
        threshold=cfg.threshold,
        minimum_region=cfg.minimum_region,
        material_mask=material_mask,
    )
    grains.provenance["annotationDomain"] = {
        **annotation,
        "policy": cfg.annotation_policy,
        "applied": material_mask is not None,
    }
    estimate = work_estimate(grains, cfg)
    _, _, _, report = run(grains, cfg.width)
    info = report["initialization"]
    border_ids = {g["id"] for g in report["grainMap"]["grains"] if g["touchesBorder"]}
    boundary_unresolved = sorted(set(info["unresolvedGrains"]) & border_ids)
    interior_unresolved = sorted(set(info["unresolvedGrains"]) - border_ids)
    resolution_notice = (
        f"Full image retained at {grains.labels.shape[1]} x {grains.labels.shape[0]} "
        "native pixels. "
        + (
            f"Border grains {boundary_unresolved} have no resolved pure interior "
            "at this interface width. "
            "Evolution is blocked until all grains are resolved."
            if boundary_unresolved
            else (
                f"Interior grains {interior_unresolved} are unresolved; evolution is blocked."
                if interior_unresolved
                else "All candidate grains have resolved pure interiors."
            )
        )
    )
    if material_mask is not None:
        resolution_notice += (
            f" {int(excluded.sum())} annotation pixels are explicitly non-material. "
            "The original canvas is retained; review the material-domain mask before approval."
        )
    if annotation_uncertain:
        resolution_notice += (
            " Possible annotation strip could not be reliably masked; evolution is blocked. "
            "Set the non-material footer height and review the mask, or select annotation "
            "handling 'none' only if the dark edge is actual material."
        )
    ready = (
        not annotation_uncertain
        and not info["unresolvedGrains"]
        and not info["argmaxMismatchPixels"]
        and info["adjacencyPreserved"]
        and info["maximumRelativeAreaChange"] <= 0.05
    )
    source_sha = hashlib.sha256(raw).hexdigest()
    review = {
        "schemaVersion": "1",
        "fingerprint": fingerprint(source_sha, config),
        "sourceSha256": source_sha,
        "configuration": config,
        "grainMap": grains.wire(compressed=True),
        "grainMapDigest": grains.digest,
        "initialization": info,
        "boundaryUnresolvedGrains": boundary_unresolved,
        "interiorUnresolvedGrains": interior_unresolved,
        "resolutionNotice": resolution_notice,
        "annotationDomain": grains.provenance["annotationDomain"],
        "fullDomain": {"shape": list(grains.labels.shape), "cropped": False, "resampled": False},
        "grainCount": len(report["grainMap"]["grains"]),
        "readyForEvolution": bool(ready),
        "budget": estimate,
        "physicalValidation": "not_performed",
        "segmentationAccuracy": "not_measured",
    }
    composite, source_roi = preview(raw, grains, cfg.crop)
    domain_preview = np.array(Image.open(BytesIO(source_roi)).convert("RGB"))
    if material_mask is not None:
        # Tint excluded pixels but leave the text/bar legible for explicit review.
        domain_preview[~material_mask] = (
            0.55 * domain_preview[~material_mask] + 0.45 * np.array([240, 170, 50])
        ).astype(np.uint8)
    arguments = {"image_base64": image_base64, "configuration": configuration}
    return envelope(
        "microstructure.prepare_showcase",
        arguments,
        [
            {"name": "segmentation_review", "kind": "json", "value": review},
            {"name": "resolution_notice", "kind": "text", "value": resolution_notice},
            file_output("segmentation_preview", composite, "image/png"),
            file_output("uploaded_image", source_roi, "image/png"),
            file_output("material_domain", png(Image.fromarray(domain_preview)), "image/png"),
            file_output("grain_labels", png_labels(grains.labels), "image/png"),
            file_output(
                "grain_map", canonical_bytes(grains.wire(compressed=True)), "application/json"
            ),
        ],
        started,
    )


def evolve_showcase(
    review: dict[str, Any],
    approved_fingerprint: str,
    image_base64: str = "",
    evolution_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evolve reviewed labels using separately recorded evolution parameters.

    Human approval is enforced by the host's human-only reviewed_result field. This
    byte/array kernel additionally checks the receipt binding; it cannot attest identity.
    """
    started = time.perf_counter()
    cfg = ShowcaseConfiguration.model_validate(review["configuration"])
    expected = fingerprint(review["sourceSha256"], cfg.model_dump())
    if approved_fingerprint != expected or review.get("fingerprint") != expected:
        raise ValueError("Reviewed image/configuration binding changed; segment and review again")
    if evolution_parameters is not None:
        overrides = EvolutionParameters.model_validate(evolution_parameters)
        cfg = ShowcaseConfiguration.model_validate({**cfg.model_dump(), **overrides.model_dump()})
    grains = GrainMap.from_wire(review["grainMap"])
    original = None
    if image_base64:
        if len(image_base64) > 14_000_000:
            raise ValueError("Encoded image exceeds showcase budget")
        raw = base64.b64decode(image_base64, validate=True)
        if hashlib.sha256(raw).hexdigest() != review["sourceSha256"]:
            raise ValueError("Original image does not match the reviewed source")
        original = np.asarray(Image.open(BytesIO(raw)).convert("RGB"))
        if original.shape[:2] != grains.labels.shape:
            raise ValueError("Original image shape does not match the reviewed canvas")
    if grains.domain_mask is not None and original is None:
        raise ValueError("Masked evolution requires the reviewed original image")
    if (
        grains.units != "pixel"
        or grains.spacing != 1.0
        or grains.digest != review.get("grainMapDigest")
    ):
        raise ValueError("Reviewed grain-map binding changed")
    if review.get("readyForEvolution") is not True:
        raise ValueError(
            "Reviewed map failed transfer gates; correct segmentation before evolution"
        )
    work_estimate(grains, cfg, enforce=True)
    arrays, report = trajectory(
        grains,
        cfg.width,
        cfg.duration,
        gamma=cfg.gamma,
        mobility=cfg.mobility,
        frame_count=FRAME_COUNT,
    )
    report["parameterBasis"] = (
        "User-reviewed hypothetical dimensionless showcase; no experimental kinetics claim"
    )
    report["reviewedFingerprint"] = expected
    report["executedConfiguration"] = cfg.model_dump()
    report["executionFingerprint"] = fingerprint(review["sourceSha256"], cfg.model_dump())
    report["fullDomain"] = {
        "shape": list(grains.labels.shape),
        "cropped": False,
        "resampled": False,
        "sourceSha256": review["sourceSha256"],
        "reviewedGrainMapDigest": grains.digest,
    }
    frames, frame_contents = [], []
    for i, (labels, density) in enumerate(
        zip(arrays["labels"], arrays["interface_density"], strict=True)
    ):
        left = Image.open(BytesIO(png_labels(labels))).convert("RGB")
        z = np.clip(density * 1.5, 0, 1)
        heat = Image.fromarray(
            np.stack((255 * z, 180 * z * z, 220 * np.sqrt(z)), axis=-1).astype("uint8")
        )
        if grains.domain_mask is not None:
            inactive = ~grains.domain_mask
            left_pixels = np.array(left)
            heat_pixels = np.array(heat)
            left_pixels[inactive] = original[inactive]
            heat_pixels[inactive] = original[inactive]
            left = Image.fromarray(left_pixels)
            heat = Image.fromarray(heat_pixels)
        frame = Image.new("RGB", (left.width * 2 + 4, left.height), (17, 23, 32))
        frame.paste(left, (0, 0))
        frame.paste(heat, (left.width + 4, 0))
        encoded = png(frame)
        frames.append(
            {
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "label": f"State {i + 1}; {len(np.unique(labels[labels > 0]))} visible grains",
                "time": float(arrays["times"][i]),
            }
        )
        frame_contents.append(content(encoded, "image/png", "sequence-frame"))
    sequence = {
        "kind": "image-sequence",
        "frames": frames,
        "timeUnit": "dimensionless",
        "description": (
            "Computed solver states: grain identities at left; "
            "diffuse interfaces at right. No interpolated frames. "
            "Excluded annotation pixels retain the original image unchanged."
        ),
    }
    # Playback owns sampled rasters. Checkpoints retain exact native initial/final fields
    # without duplicating every raster in a second giant transport payload.
    checkpoint = {k: v for k, v in arrays.items() if k.startswith(("initial", "final"))}
    checkpoint.update(
        grain_ids=arrays["grain_ids"],
        initial_labels=arrays["labels"][0],
        final_labels=arrays["labels"][-1],
        time=arrays["times"][-1:],
    )
    outputs = [
        {"name": "evolution_report", "kind": "json", "value": report},
        file_output(
            "evolution_sequence", canonical_bytes(sequence), "application/json", "sequence"
        ),
        file_output("evolution_checkpoint", trajectory_npz(checkpoint), "application/x-npz"),
    ]
    for i in range(0, len(frame_contents), 16):
        outputs.append(
            {
                "name": f"evolution_frames_{i // 16 + 1}",
                "kind": "artifact",
                "artifacts": frame_contents[i : i + 16],
            }
        )
    history = report["trajectory"]
    table = {
        "columns": ["Time (dimensionless)", "Visible grains", "Free energy"],
        "rows": [
            [t, n, e]
            for t, n, e in zip(
                history["times"], history["visibleGrainCounts"], history["energy"], strict=True
            )
        ],
    }
    area_table = {
        "columns": ["Time (dimensionless)"] + [f"Grain {i} area" for i in history["grainIds"]],
        "rows": [
            [t, *areas] for t, areas in zip(history["times"], history["diffuseAreas"], strict=True)
        ],
    }
    area_array = np.asarray(history["diffuseAreas"])
    fractional_loss = (area_array[0] - area_array[-1]) / area_array[0]
    # One representative curve keeps the workspace bounded; the table retains every grain.
    selected_grains = np.argsort(-fractional_loss, kind="stable")[:1]
    curves = []
    for index in selected_grains:
        curve = {
            "title": f"Grain {history['grainIds'][index]} diffuse area",
            "xLabel": "Time (dimensionless)",
            "yLabel": "Area (pixel squared)",
            "x": history["times"],
            "y": area_array[:, index].tolist(),
        }
        curves.append(content(canonical_bytes(curve), "application/json"))
    outputs.extend(
        [
            file_output("evolution_history", canonical_bytes(table), "application/json"),
            file_output("grain_area_history", canonical_bytes(area_table), "application/json"),
            file_output(
                "free_energy_curve",
                canonical_bytes(
                    {
                        "title": "Free energy",
                        "xLabel": "Time (dimensionless)",
                        "yLabel": "Energy (dimensionless)",
                        "x": history["times"],
                        "y": history["energy"],
                    }
                ),
                "application/json",
            ),
            {"name": "shrinking_grain_curve", "kind": "artifact", "artifacts": curves},
        ]
    )
    return envelope(
        "microstructure.evolve_showcase",
        {
            "review": review,
            "approved_fingerprint": approved_fingerprint,
            "image_base64": image_base64,
            "evolution_parameters": evolution_parameters,
        },
        outputs,
        started,
        fields=table,
    )


def build_server():
    server = MCPServer(
        "consilith-microstructure-platform",
        version=implementation_identity(__file__, __version__),
        instructions=(
            "Prepare uploaded clear-grain images; require human review before evolution. "
            "All kinetics are hypothetical dimensionless scenarios; no SAM."
        ),
    )
    server.tool(name="microstructure.prepare_showcase")(prepare_showcase)
    server.tool(name="microstructure.evolve_showcase")(evolve_showcase)
    return server


def app():
    return build_server().streamable_http_app(
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "solver-microstructure:*",
                "microstructure:*",
            ],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
