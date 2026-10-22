"""Grain growth on a coarser simulation grid, shown at the native size.

Curvature-driven growth needs a time of the order of the mean grain area before grains visibly
disappear, and every solver step touches every pixel. A grain map with grains hundreds of pixels
across is therefore evolved on a grid coarsened by an integer factor ``f``: the labels are
sampled at block centres (no new labels, no interpolation of ids), the run is chosen long enough
to coarsen the structure (about three mean grain areas over mobility times energy), and the
frames are scaled back to the native canvas for display. Grain ids, the material-domain mask
and the original pixels outside it are kept. All of it is recorded in the report.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage.filters.rank import modal
from skimage.morphology import disk

from .data import GrainMap

# A coarse grain of about this many pixels is still resolved by a 4-pixel diffuse interface.
TARGET_COARSE_AREA = 300.0
MINIMUM_COARSE_AREA = 110.0
MAXIMUM_FACTOR = 8
# Simulated time per mean grain area (over mobility x energy): about two thirds of the grains
# coarsen away, measured on clear-boundary micrographs of 40-60 grains.
EXTENT_PER_AREA = 3.0
# Coarse pixels x solver steps an automatic run may take (about half a minute on 4 cores).
AUTO_WORK_BUDGET = 600_000_000


def mean_area(labels: np.ndarray) -> float:
    ids, counts = np.unique(labels[labels > 0], return_counts=True)
    return float(counts.mean()) if len(ids) else 0.0


def coarse_map(grains: GrainMap, factor: int) -> GrainMap:
    """Labels sampled at block centres; the domain mask sampled the same way."""
    if factor == 1:
        return grains
    start = factor // 2
    labels = grains.labels[start::factor, start::factor].copy()
    mask = None
    if grains.domain_mask is not None:
        mask = grains.domain_mask[start::factor, start::factor].copy()
        labels[~mask] = 0
    return GrainMap(labels, spacing=1.0, units="pixel", domain_mask=mask)


def plan(grains: GrainMap, *, gamma: float, mobility: float, dt_limit) -> dict:
    """The factor and the duration of an automatic run. ``dt_limit`` is the solver's stable
    step on a unit grid for these parameters; the plan raises the factor until the work fits the
    budget, never below a resolved grain."""
    native = mean_area(grains.labels)
    if native <= 0:
        raise ValueError("no grains to evolve")
    factor = int(max(1, min(MAXIMUM_FACTOR, round(np.sqrt(native / TARGET_COARSE_AREA)))))
    while True:
        coarse = native / factor**2
        duration = EXTENT_PER_AREA * coarse / (mobility * gamma)
        pixels = grains.labels.size / factor**2
        steps = int(np.ceil(duration / dt_limit))
        work = pixels * steps
        bigger = native / (factor + 1) ** 2
        if work <= AUTO_WORK_BUDGET or factor >= MAXIMUM_FACTOR or bigger < MINIMUM_COARSE_AREA:
            return {
                "factor": factor,
                "duration": duration,
                "nativeMeanGrainArea": native,
                "coarseMeanGrainArea": coarse,
                "estimatedSolverSteps": steps,
                "pixelSteps": int(work),
                "withinBudget": work <= AUTO_WORK_BUDGET,
            }
        factor += 1


def _upsample(array: np.ndarray, factor: int, shape: tuple[int, int]) -> np.ndarray:
    big = np.repeat(np.repeat(array, factor, axis=0), factor, axis=1)
    pad = [(0, max(0, shape[0] - big.shape[0])), (0, max(0, shape[1] - big.shape[1]))]
    if any(p[1] for p in pad):
        big = np.pad(big, pad, mode="edge")
    return big[: shape[0], : shape[1]]


def native_labels(
    labels: np.ndarray, factor: int, shape: tuple[int, int], mask: np.ndarray | None
) -> np.ndarray:
    """A coarse label frame on the native canvas: each block takes its sample's id; native
    domain pixels the coarse grid missed take the nearest grain; outside the domain is 0."""
    if factor == 1:
        return labels
    big = _upsample(labels, factor, shape).astype(np.int32)
    if big.max() < 65536:
        # The most common id in a disk the size of a block: smooth grain edges instead of
        # coarse-grid stairs. Ids only move between neighbours already touching there.
        big = modal(big.astype(np.uint16), disk(factor)).astype(np.int32)
    domain = np.ones(shape, bool) if mask is None else mask
    holes = domain & (big == 0)
    if holes.any() and (big > 0).any():
        _, (iy, ix) = ndimage.distance_transform_edt(big == 0, return_indices=True)
        big[holes] = big[iy[holes], ix[holes]]
    big[~domain] = 0
    return big


def native_density(density: np.ndarray, factor: int, shape: tuple[int, int]) -> np.ndarray:
    """Interface density on the native canvas, smoothly interpolated (display only)."""
    if factor == 1:
        return density
    zoomed = ndimage.zoom(density.astype(np.float32), factor, order=1)
    return _upsample(zoomed, 1, shape) if zoomed.shape != shape else zoomed


def to_native(arrays: dict, report: dict, factor: int, grains: GrainMap) -> None:
    """Rewrite a coarse trajectory's frames, areas and energies for the native canvas, in
    place, and record how. Times stay in coarse-grid units; the report gives the scale."""
    if factor == 1:
        return
    shape = grains.labels.shape
    mask = grains.domain_mask
    arrays["labels"] = np.stack(
        [native_labels(frame, factor, shape, mask) for frame in arrays["labels"]]
    )
    arrays["interface_density"] = np.stack(
        [native_density(frame, factor, shape) for frame in arrays["interface_density"]]
    )
    arrays["diffuse_areas"] = arrays["diffuse_areas"] * factor**2
    trajectory = report["trajectory"]
    trajectory["diffuseAreas"] = [
        [a * factor**2 for a in row] for row in trajectory["diffuseAreas"]
    ]
    trajectory["energy"] = [e * factor for e in trajectory["energy"]]
    report["simulationGrid"] = {
        "coarsening": factor,
        "nativeShape": list(shape),
        "simulatedShape": [int(np.ceil(shape[0] / factor)), int(np.ceil(shape[1] / factor))],
        "labels": "sampled at block centres; frames repeated back to the native canvas",
        "areas": "native pixels (coarse areas x factor squared)",
        "energy": "native-pixel interface length (coarse energy x factor)",
        "time": "dimensionless on the coarse grid; native-grid equivalent is time x factor squared",
        "why": "grain growth needs about one mean grain area of time to show; a coarser grid "
        "reaches it in seconds instead of minutes",
    }
