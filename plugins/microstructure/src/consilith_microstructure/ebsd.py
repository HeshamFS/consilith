"""Explicit square-grid ANG/CTF subset; orientations never inferred from image colors."""

import hashlib
import re

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial.transform import Rotation

from .data import MAX_PIXELS, GrainMap, positive


def from_orientations(
    x, y, euler, phase, *, threshold_deg=10.0, symmetry="cubic", length_unit="um", provenance=None
):
    threshold_deg = positive(threshold_deg, "misorientation threshold")
    if threshold_deg > 180 or symmetry not in {"cubic", "triclinic"}:
        raise ValueError("supported symmetries: cubic, triclinic; threshold <=180 degrees")
    scale = {"um": 1e-6, "m": 1.0}.get(length_unit)
    if scale is None:
        raise ValueError("explicit coordinate unit required: um or m")
    x, y, euler, phase = map(np.asarray, (x, y, euler, phase))
    n = len(x)
    if not 4 <= n <= MAX_PIXELS or y.shape != (n,) or phase.shape != (n,) or euler.shape != (n, 3):
        raise ValueError("invalid EBSD column shapes or size")
    if (
        not all(np.isfinite(v).all() for v in (x, y, euler, phase))
        or np.any(phase < 0)
        or np.any(phase != np.floor(phase))
    ):
        raise ValueError("invalid EBSD numeric values or phase IDs")
    xs = np.unique(x)
    ys = np.unique(y)
    if len(xs) < 2 or len(ys) < 2 or len(xs) * len(ys) != n:
        raise ValueError(
            "only complete rectangular square sampling is supported; no implicit hole filling"
        )
    hx = np.diff(xs)
    hy = np.diff(ys)
    if not (
        np.allclose(hx, hx[0], rtol=1e-5, atol=1e-12)
        and np.allclose(hy, hx[0], rtol=1e-5, atol=1e-12)
    ):
        raise ValueError("non-square or nonuniform grid requires explicit resampling")
    order = np.lexsort((x, y))
    x = x[order]
    y = y[order]
    euler = euler[order]
    phase = phase[order]
    if len(np.unique(np.column_stack((x, y)), axis=0)) != n:
        raise ValueError("duplicate EBSD coordinates")
    h, w = len(ys), len(xs)
    grid = np.arange(n).reshape(h, w)
    left = np.concatenate((grid[:, :-1].ravel(), grid[:-1].ravel()))
    right = np.concatenate((grid[:, 1:].ravel(), grid[1:].ravel()))
    ok = (phase[left] > 0) & (phase[left] == phase[right])
    left, right = left[ok], right[ok]
    # Bunge intrinsic ZXZ, radians. Misorientation minimized over crystal symmetry.
    rotations = Rotation.from_euler("ZXZ", euler)
    relative = rotations[left].inv() * rotations[right]
    sym = Rotation.create_group("O") if symmetry == "cubic" else Rotation.identity(1)
    angles = np.full(len(left), np.inf)
    for s in sym:
        angles = np.minimum(angles, (relative * s).magnitude())
    same = angles <= np.deg2rad(threshold_deg) + 1e-12
    left, right = left[same], right[same]
    graph = coo_matrix(
        (np.ones(2 * len(left)), (np.r_[left, right], np.r_[right, left])), shape=(n, n)
    )
    _, labels = connected_components(graph, directed=False)
    valid = phase > 0
    unique = np.unique(labels[valid])
    remap = {key: i + 1 for i, key in enumerate(unique)}
    labels = np.array(
        [remap[k] if v else 0 for k, v in zip(labels, valid, strict=True)], dtype=np.int32
    ).reshape(h, w)
    meta = dict(provenance or {})
    meta.update(
        {
            "method": "ebsd-symmetry-components-v1",
            "thresholdDegrees": threshold_deg,
            "symmetry": symmetry,
            "phaseIds": np.unique(phase[valid]).astype(int).tolist(),
            "eulerConvention": "intrinsic ZXZ radians",
            "coordinateUnit": length_unit,
            "orientationSha256": hashlib.sha256(euler.astype("<f8").tobytes()).hexdigest(),
            "status": "candidate_requires_review",
        }
    )
    meta["origin"] = [float(xs[0] * scale), float(ys[0] * scale)]
    flat = labels.ravel()
    meta["phaseByGrain"] = {
        str(i): int(phase[np.flatnonzero(flat == i)[0]]) for i in np.unique(flat) if i > 0
    }
    meta["representativeOrientationByGrain"] = {
        str(i): {
            "sourcePointIndexSorted": int(np.flatnonzero(flat == i)[0]),
            "quaternionXYZW": rotations[np.flatnonzero(flat == i)[0]].as_quat().tolist(),
        }
        for i in np.unique(flat)
        if i > 0
    }
    return GrainMap(labels, float(hx[0] * scale), "m", meta)


def read_ebsd(raw, format, *, threshold_deg=10.0, symmetry="cubic", length_unit="um"):
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("EBSD file exceeds 20 MiB")
    text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    if format == "ang":
        grid = [line for line in lines if "GRID:" in line.upper()]
        if grid and "SQRGRID" not in grid[0].upper():
            raise ValueError("hexagonal ANG grid unsupported")
        rows = [
            list(map(float, line.split()))
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
        a = np.asarray(rows)
        if a.ndim != 2 or a.shape[1] < 8:
            raise ValueError("ANG needs Euler, x/y, IQ, CI and phase columns")
        eu = a[:, :3]
        x = a[:, 3]
        y = a[:, 4]
        phase = a[:, 7].copy()
        phase[a[:, 6] < 0] = 0
        quality = {
            "iqRange": [float(a[:, 5].min()), float(a[:, 5].max())],
            "ciRange": [float(a[:, 6].min()), float(a[:, 6].max())],
        }
    elif format == "ctf":
        index = next((i for i, s in enumerate(lines) if re.match(r"^Phase\s+X\s+Y\s+", s)), None)
        if index is None:
            raise ValueError("missing CTF data header")
        header = lines[index].split()
        required = ["Phase", "X", "Y", "Euler1", "Euler2", "Euler3"]
        if not all(v in header for v in required):
            raise ValueError("missing CTF columns")
        a = np.array([list(map(float, s.split())) for s in lines[index + 1 :] if s.strip()])
        if a.ndim != 2 or a.shape[1] != len(header):
            raise ValueError("inconsistent CTF columns")

        def col(k):
            return a[:, header.index(k)]

        phase = col("Phase")
        x = col("X")
        y = col("Y")
        eu = np.deg2rad(np.column_stack([col(k) for k in required[3:]]))
        quality = {}
    else:
        raise ValueError("supported raw EBSD formats: ang, ctf")
    return from_orientations(
        x,
        y,
        eu,
        phase,
        threshold_deg=threshold_deg,
        symmetry=symmetry,
        length_unit=length_unit,
        provenance={
            "sourceSha256": hashlib.sha256(raw).hexdigest(),
            "format": format,
            "quality": quality,
        },
    )
