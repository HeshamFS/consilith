"""Deterministic boundary-based reconstruction, not a universal learned segmenter."""

import hashlib
from io import BytesIO

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage.filters import sato, threshold_local, threshold_otsu
from skimage.morphology import disk
from skimage.segmentation import watershed

from .data import MAX_PIXELS, GrainMap


def _close_boundary(mask, radius):
    # Reflecting extension avoids joining distinct grains around a zero-padded outer rim.
    pad = radius + 1
    padded = np.pad(mask, pad, mode="edge")
    return ndi.binary_closing(padded, structure=disk(radius))[pad:-pad, pad:-pad]


def segment_image(
    raw: bytes,
    *,
    crop=None,
    spacing=1.0,
    units="pixel",
    boundary="dark",
    threshold=None,
    minimum_region=12,
    method="global",
    smoothing=1.0,
    local_window=51,
    local_offset=0.02,
    ridge_threshold=0.15,
    closing_radius=1,
    markers=None,
    excluded_mask=None,
    material_mask=None,
):
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise ValueError("image must be nonempty and <=10 MiB")
    if boundary not in {"dark", "bright"} or not 1 <= minimum_region <= MAX_PIXELS:
        raise ValueError("invalid boundary polarity or minimum region")
    if method not in {"global", "adaptive", "ridge", "guided"}:
        raise ValueError("unknown segmentation method")
    if type(minimum_region) is not int:
        raise ValueError("minimum_region must be an integer")
    if not np.isfinite(smoothing) or not 0 <= smoothing <= 8:
        raise ValueError("smoothing must be finite in 0..8 pixels")
    if type(local_window) is not int or not 3 <= local_window <= 255 or local_window % 2 == 0:
        raise ValueError("local_window must be an odd integer in 3..255")
    if not np.isfinite(local_offset) or not 0 <= local_offset < 1:
        raise ValueError("local_offset must lie in [0,1)")
    if not np.isfinite(ridge_threshold) or not 0 < ridge_threshold < 1:
        raise ValueError("ridge_threshold must lie in (0,1)")
    if type(closing_radius) is not int or not 0 <= closing_radius <= 8:
        raise ValueError("closing_radius must be an integer in 0..8 pixels")
    if method != "global" and threshold is not None:
        raise ValueError("threshold is only used by the global method")
    if method != "guided" and markers is not None:
        raise ValueError("markers require the guided method")
    with Image.open(BytesIO(raw)) as img:
        if img.format not in {"PNG", "JPEG", "TIFF"} or getattr(img, "n_frames", 1) != 1:
            raise ValueError("only single-frame PNG/JPEG/TIFF are supported")
        if img.width * img.height > MAX_PIXELS:
            raise ValueError("decoded image exceeds pixel budget")
        if img.getexif().get(274, 1) != 1:
            raise ValueError("apply and record EXIF orientation before ingestion")
        original_size = list(img.size)
        if crop is not None:
            if len(crop) != 4 or any(type(v) is not int for v in crop):
                raise ValueError("crop must contain four integer pixel coordinates")
            x0, y0, x1, y1 = crop
            if not (0 <= x0 < x1 <= img.width and 0 <= y0 < y1 <= img.height):
                raise ValueError("crop is outside the image")
            img = img.crop(tuple(crop))
        if img.mode in {"P", "PA", "LA", "RGBA"}:
            rgba = np.asarray(img.convert("RGBA"))
            if np.any(rgba[:, :, 3] != 255):
                raise ValueError("transparent pixels need an explicit valid-region policy")
            img = img.convert("RGB")
        a = np.asarray(img)
        if a.ndim == 3:
            a = np.asarray(img.convert("RGB"), dtype=float) @ np.array([0.2126, 0.7152, 0.0722])
        a = a.astype(float)
    if min(a.shape) < 2 or not np.isfinite(a).all() or np.ptp(a) == 0:
        raise ValueError("image must contain finite contrast")
    a = (a - a.min()) / np.ptp(a)
    valid = np.ones(a.shape, dtype=bool)
    if material_mask is not None:
        material_mask = np.asarray(material_mask)
        if material_mask.shape != a.shape or material_mask.dtype != bool or not material_mask.any():
            raise ValueError("material_mask must be a nonempty boolean raster matching the image")
        valid &= material_mask
    if excluded_mask is not None:
        excluded = np.asarray(excluded_mask)
        if excluded.shape != a.shape or excluded.dtype != bool:
            raise ValueError("excluded_mask must be a boolean raster matching the cropped image")
        valid &= ~excluded
        if not valid.any():
            raise ValueError("no valid image pixels")
    signal = 1 - a if boundary == "dark" else a
    smooth = ndi.gaussian_filter(signal, smoothing) if smoothing else signal
    removed = 0
    t = None
    if method == "global":
        t = float(threshold_otsu(a[valid]) if threshold is None else threshold)
        if not np.isfinite(t) or not 0 < t < 1:
            raise ValueError("normalized threshold must lie strictly between zero and one")
        boundary_mask = a < t if boundary == "dark" else a > t
        cost = signal
    elif method == "adaptive":
        local = threshold_local(smooth, local_window, method="gaussian")
        boundary_mask = smooth > local + local_offset
        if closing_radius:
            boundary_mask = _close_boundary(boundary_mask, closing_radius)
        cost = smooth
    else:
        ridge = sato(smooth, sigmas=(1, 2, 3), black_ridges=False)
        scale = np.percentile(ridge[valid], 99.5)
        if scale <= np.finfo(float).eps:
            raise ValueError("no usable ridge contrast")
        cost = np.clip(ridge / scale, 0, 1)
        boundary_mask = cost > ridge_threshold
        if method == "ridge" and closing_radius:
            boundary_mask = _close_boundary(boundary_mask, closing_radius)
    if method == "guided":
        points = np.asarray(markers)
        if (
            points.ndim != 2
            or points.shape[1] != 2
            or not 2 <= len(points) <= 512
            or not np.issubdtype(points.dtype, np.integer)
        ):
            raise ValueError("guided markers must be 2..512 integer [x,y] interior points")
        if (
            np.any(points < 0)
            or np.any(points[:, 0] >= a.shape[1])
            or np.any(points[:, 1] >= a.shape[0])
        ):
            raise ValueError("marker is outside the cropped image")
        if len(np.unique(points, axis=0)) != len(points):
            raise ValueError("duplicate markers")
        if not valid[points[:, 1], points[:, 0]].all():
            raise ValueError("marker lies in an excluded region")
        seeds = np.zeros(a.shape, dtype=np.int32)
        for i, (x, y) in enumerate(points, 1):
            seeds[y, x] = i
    else:
        seeds, _ = ndi.label(~boundary_mask & valid)
        sizes = np.bincount(seeds.ravel())
        keep = sizes >= minimum_region
        keep[0] = False
        removed = int((~keep[1:]).sum())
        seeds[~keep[seeds]] = 0
    ids = np.unique(seeds)
    ids = ids[ids > 0]
    if len(ids) < 2 or len(ids) > 512:
        raise ValueError(
            "no usable multi-grain partition: needs 2..512 distinct interior markers; "
            "review segmentation"
        )
    seed_labels = np.zeros_like(seeds)
    for i, key in enumerate(ids, 1):
        seed_labels[seeds == key] = i
    labels = watershed(cost, markers=seed_labels, connectivity=1, mask=valid).astype(np.int32)
    # Valid islands without markers stay unknown; never invent a grain for them.
    sizes = np.bincount(labels.ravel())[1:]
    return GrainMap(
        labels,
        spacing,
        units,
        {
            "method": method + "-watershed-v2",
            "parameters": {
                "smoothingPixels": smoothing if method != "global" else 0,
                "localWindow": local_window if method == "adaptive" else None,
                "localOffset": local_offset if method == "adaptive" else None,
                "closingRadius": closing_radius if method in {"adaptive", "ridge"} else None,
                "ridgeThreshold": ridge_threshold if method == "ridge" else None,
                "ridgeSigmas": [1, 2, 3] if method in {"ridge", "guided"} else None,
            },
            "markerPoints": np.asarray(markers).tolist() if method == "guided" else None,
            "excludedMaskSha256": (
                hashlib.sha256((~valid).tobytes()).hexdigest()
                if excluded_mask is not None
                else None
            ),
            "domainMaskSha256": (
                hashlib.sha256(material_mask.astype(np.uint8).tobytes()).hexdigest()
                if material_mask is not None
                else None
            ),
            "excludedPixels": int((~valid).sum()),
            "unassignedValidPixels": int(((labels == 0) & valid).sum()),
            "quality": {
                "largestRegionFraction": float(sizes.max() / valid.sum()),
                "regionsBelowMinimumPixels": int((sizes < minimum_region).sum()),
                "materialModelEligibility": "not_assessed",
                "warning": "Regions may be pores, phases or artifacts; review the source overlay.",
            },
            "sourceSha256": hashlib.sha256(raw).hexdigest(),
            "originalSize": original_size,
            "crop": crop,
            "normalizedThreshold": t,
            "polarity": boundary,
            "minimumMarkerPixels": minimum_region,
            "discardedMarkers": removed,
            "estimatedBoundaryPixels": int(boundary_mask.sum()),
            "status": "candidate_requires_review",
            "groundTruthAccuracy": "not_measured",
        },
        domain_mask=material_mask,
    )
