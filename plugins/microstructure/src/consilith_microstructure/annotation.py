"""Conservative proposals for obvious black acquisition strips, not pore detection.

Only full-width top/bottom bands are considered. The original raster is retained;
the returned boolean mask must be displayed and reviewed before use in a model.
"""

import hashlib
from io import BytesIO

import numpy as np
from PIL import Image

from .data import MAX_PIXELS


def detect_annotation_mask(raw: bytes):
    """Return a full-canvas excluded-pixel proposal and auditable detection report.

    No semantic classifier can prove that a dark strip is nonmaterial from pixels
    alone. This deliberately refuses ambiguous dark fields and unmarked bands.

    ``annotation_review_required`` takes precedence whenever either edge has
    annotation-like dark background and bright marks but no unique supported
    boundary. The caller must block automatic material inference in that case.
    ``no_confident_candidate`` means no such evidence was found, not proof that
    annotations are absent. Even ``candidate_requires_review`` is only a proposal.
    """
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise ValueError("image must be nonempty and <=10 MiB")
    with Image.open(BytesIO(raw)) as image:
        if image.format not in {"PNG", "JPEG", "TIFF"} or getattr(image, "n_frames", 1) != 1:
            raise ValueError("only single-frame PNG/JPEG/TIFF are supported")
        if image.width * image.height > MAX_PIXELS or min(image.size) < 2:
            raise ValueError("image exceeds pixel budget or is too small")
        if image.getexif().get(274, 1) != 1:
            raise ValueError("apply and record EXIF orientation before ingestion")
        if image.mode not in {"L", "RGB", "RGBA", "P", "LA"}:
            raise ValueError("annotation detection requires an 8-bit grayscale or color image")
        rgba = np.asarray(image.convert("RGBA"))
        if np.any(rgba[:, :, 3] != 255):
            raise ValueError("transparent pixels need an explicit valid-region policy")
        gray = np.asarray(image.convert("L"), dtype=float)
    height, width = gray.shape
    excluded = np.zeros(gray.shape, dtype=bool)
    threshold = 32
    dark = gray <= threshold
    fractions = dark.mean(axis=1)
    midgray_fractions = ((gray > threshold) & (gray < 192)).mean(axis=1)
    min_rows = max(8, int(np.ceil(height * 0.01)))
    max_rows = int(np.floor(height * 0.20))
    # Evaluate complete edge bands at abrupt material/background transitions.
    # Do not stop at a bright scale-bar row: its fractional width changes under
    # user cropping even though its annotation meaning does not change.
    row_medians = np.median(gray, axis=1)
    dark_prefix = np.r_[0.0, np.cumsum(fractions)]
    strong_prefix = np.r_[0, np.cumsum(fractions >= 0.95)]
    bright_prefix = np.r_[0, np.cumsum((gray >= 192).sum(axis=1))]
    minimum_marks = max(8, int(width * 0.01))
    candidates = []
    rejected = []
    suspected_edges = []
    for edge in ("top", "bottom"):
        accepted = []
        for count in range(min_rows, max_rows + 1):
            start, stop = (0, count) if edge == "top" else (height - count, height)
            strong_rows = strong_prefix[stop] - strong_prefix[start]
            band_dark_fraction = (dark_prefix[stop] - dark_prefix[start]) / count
            band_bright_marks = bright_prefix[stop] - bright_prefix[start]
            if (
                edge not in suspected_edges
                and strong_rows >= min_rows
                and band_bright_marks >= minimum_marks
                and band_dark_fraction >= 0.50
            ):
                suspected_edges.append(edge)
            inner_row = stop - 1 if edge == "top" else start
            boundary_row = stop if edge == "top" else start - 1
            if fractions[inner_row] < 0.95:
                continue
            transition = float(row_medians[boundary_row] - row_medians[inner_row])
            if transition < 48 or row_medians[boundary_row] < 64:
                continue
            strong_fraction = float((strong_prefix[stop] - strong_prefix[start]) / count)
            dark_fraction = float((dark_prefix[stop] - dark_prefix[start]) / count)
            bright_marks = int(bright_prefix[stop] - bright_prefix[start])
            reasons = []
            if strong_fraction < 0.50 or dark_fraction < 0.75:
                reasons.append("insufficient_uniform_nearblack_background")
            if bright_marks < minimum_marks:
                reasons.append("no_bright_annotation_marks")
            if np.max(midgray_fractions[start:stop]) > 0.25:
                reasons.append("band_contains_material_like_midgray_rows")
            if reasons:
                rejected.append({"edge": edge, "rows": count, "reason": ";".join(reasons)})
                continue
            accepted.append(
                {
                    "edge": edge,
                    "rows": [start, stop],
                    "rows_convention": "zero_based_half_open",
                    "dark_fraction": dark_fraction,
                    "strong_row_fraction": strong_fraction,
                    "boundary_median_jump_8bit": transition,
                    "bright_mark_pixels": bright_marks,
                }
            )
        if len(accepted) == 1:
            if edge not in suspected_edges:
                suspected_edges.append(edge)
            candidates.append(accepted[0])
        elif len(accepted) > 1:
            # A bright bar or a real horizontal material boundary can yield
            # alternative extents. Never silently erase the larger region.
            if edge not in suspected_edges:
                suspected_edges.append(edge)
            rejected.append(
                {
                    "edge": edge,
                    "reason": "ambiguous_multiple_supported_boundaries",
                    "alternative_rows": [item["rows"] for item in accepted],
                }
            )
        elif edge in suspected_edges:
            rejected.append(
                {"edge": edge, "reason": "annotation_like_edge_without_confident_full_boundary"}
            )
    if sum(item["rows"][1] - item["rows"][0] for item in candidates) > max_rows:
        rejected.append({"edge": "combined", "reason": "combined_strips_exceed_20_percent"})
        candidates = []
    unresolved_edges = [
        edge for edge in suspected_edges if not any(item["edge"] == edge for item in candidates)
    ]
    for item in candidates:
        start, stop = item["rows"]
        excluded[start:stop, :] = True
    report = {
        "method": "conservative_edge_annotation_strip_v2",
        "status": (
            "annotation_review_required"
            if unresolved_edges
            else "candidate_requires_review"
            if candidates
            else "no_confident_candidate"
        ),
        "requires_review": True,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "mask_sha256": hashlib.sha256(excluded.tobytes(order="C")).hexdigest(),
        "mask_encoding": "row_major_boolean_bytes_0_material_1_excluded",
        "original_size": [width, height],
        "mask_shape": [height, width],
        "canvas_preserved": True,
        "cropped": False,
        "resampled": False,
        "excluded_pixels": int(excluded.sum()),
        "excluded_fraction": float(excluded.mean()),
        "candidate_bands": candidates,
        "rejected_candidates": rejected,
        "suspected_edges": suspected_edges,
        "unresolved_edges": unresolved_edges,
        "parameters": {
            "nearblack_threshold_8bit": threshold,
            "minimum_boundary_row_dark_fraction": 0.95,
            "strong_row_dark_fraction": 0.95,
            "minimum_strong_rows_fraction": 0.50,
            "minimum_band_dark_fraction": 0.75,
            "maximum_each_row_midgray_fraction": 0.25,
            "ambiguous_edge_minimum_dark_fraction": 0.50,
            "minimum_transition_8bit": 48,
            "minimum_material_boundary_median_8bit": 64,
            "bright_mark_threshold_8bit": 192,
            "minimum_bright_mark_pixels": minimum_marks,
            "minimum_height_pixels": min_rows,
            "maximum_combined_height_fraction": 0.20,
        },
        "limitation": (
            "Pixel heuristic only, not annotation ground truth or pore identification. "
            "Confirm the overlay before approval; disable this proposal if it excludes material. "
            "An annotation_review_required status blocks automatic material inference until "
            "an explicit material domain is supplied. No candidate does not prove the image "
            "has no annotations."
        ),
    }
    return excluded, report
