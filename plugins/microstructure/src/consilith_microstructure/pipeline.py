"""Explicit transforms and bounded standalone operations shared by CLI and MCP."""

import base64
import hashlib
from dataclasses import asdict
from io import BytesIO

import numpy as np
from PIL import Image

from . import __version__
from .data import GrainMap
from .ebsd import read_ebsd
from .fields import initialize
from .imaging import segment_image
from .solver import Parameters, evolve
from .sparse import arrays_for_fields


def resample(grains, factor):
    if type(factor) is not int or not 1 <= factor <= 16:
        raise ValueError("resample factor must be an integer in 1..16")
    if factor == 1:
        return grains
    if getattr(grains, "domain_mask", None) is not None:
        raise ValueError("masked material domains must retain their native resolution")
    h, w = grains.labels.shape
    if h % factor or w % factor:
        raise ValueError("crop dimensions must be divisible by resample factor")
    labels = grains.labels[factor // 2 :: factor, factor // 2 :: factor]
    if not np.array_equal(np.unique(labels), np.unique(grains.labels)):
        raise ValueError("resampling would lose a grain; choose finer grid")
    recovered = np.repeat(np.repeat(labels, factor, axis=0), factor, axis=1)
    errors = [
        abs(float((labels == i).sum() * factor**2 / (grains.labels == i).sum() - 1))
        for i in np.unique(labels)
        if i > 0
    ]
    return GrainMap(
        labels,
        grains.spacing * factor,
        grains.units,
        {
            **grains.provenance,
            "parentDigest": grains.digest,
            "resampling": {
                "method": "cell-center-nearest",
                "factor": factor,
                "sourceShape": [h, w],
                "sourcePixelMismatchFraction": float(np.mean(recovered != grains.labels)),
                "maximumRelativeGrainAreaChange": max(errors, default=0),
            },
        },
    )


def png_labels(labels):
    ids = np.asarray(labels)
    colors = np.stack(
        ((ids * 71) % 223 + 32, (ids * 137) % 223 + 32, (ids * 193) % 223 + 32), axis=-1
    ).astype("uint8")
    colors[ids == 0] = 0
    im = Image.fromarray(colors)
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue()


def artifact(raw, media_type):
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mediaType": media_type,
        "contentBase64": base64.b64encode(raw).decode(),
    }


def reconstruct(raw, *, kind="image", factor=1, **options):
    grains = segment_image(raw, **options) if kind == "image" else read_ebsd(raw, kind, **options)
    simulation_map = resample(grains, factor)
    return grains, simulation_map


def run(grains, width, *, duration=0.0, gamma=1.0, mobility=1.0, boundary="noflux", dt=None):
    phases = grains.provenance.get("phaseIds", [])
    if len(phases) > 1:
        raise ValueError(
            "multiphase EBSD cannot run in a single-solid-phase model; "
            "select a valid single-phase ROI"
        )
    phi, ids, initial = initialize(grains, width)
    if duration > 0:
        if initial["unresolvedGrains"]:
            raise ValueError(
                "unresolved grains lack a pure interior; refine grid/width before simulation"
            )
        if initial["argmaxMismatchPixels"] or not initial["adjacencyPreserved"]:
            raise ValueError("grain topology changed during initialization")
        if initial["maximumRelativeAreaChange"] > 0.05:
            raise ValueError("initial diffuse area change exceeds the 5% transfer tolerance")
    if boundary == "periodic":
        raise ValueError(
            "pixel initializer uses nonperiodic distances; periodic initial fields "
            "must be supplied to the kernel explicitly"
        )
    p = Parameters(
        grains.spacing,
        width,
        gamma,
        mobility,
        boundary,
        "SI" if grains.units == "m" else "dimensionless",
    )
    final, diagnostics = evolve(phi, p, duration, dt)
    return (
        phi,
        final,
        ids,
        {
            "schemaVersion": "1",
            "implementationVersion": __version__,
            "grainMap": grains.report(),
            "initialization": initial,
            "parameters": asdict(p),
            "simulation": diagnostics,
            "physicalValidation": "not_performed",
            "segmentationAccuracy": "not_measured",
        },
    )


def bundle_arrays(labels, ids, initial, final):
    b = BytesIO()
    np.savez_compressed(
        b,
        labels=labels,
        grain_ids=ids,
        **arrays_for_fields("initial", initial),
        **arrays_for_fields("final", final),
    )
    return b.getvalue()
