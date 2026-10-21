"""Conversion retains the accepted pixel partition and quantifies diffuse area changes."""

import numpy as np
from scipy import ndimage as ndi

from .data import MAX_FIELD_VALUES, GrainMap, positive


def profile(distance, width):
    width = positive(width, "interface width")
    return 0.5 * (1 + np.sin(np.pi * np.clip(np.asarray(distance) / width, -0.5, 0.5)))


def initialize(grains: GrainMap, width: float, *, storage="auto"):
    width = positive(width, "interface width")
    if width < 4 * grains.spacing:
        raise ValueError("interface width must span at least four grid cells")
    labels = grains.labels
    domain = getattr(grains, "domain_mask", None)
    if np.any((labels == 0) & (True if domain is None else domain)):
        raise ValueError("unknown pixels cannot be silently assigned to grains")
    ids = np.unique(labels[labels > 0])
    if storage not in {"auto", "dense", "sparse"}:
        raise ValueError("unknown field storage")
    if (
        domain is not None
        or storage == "sparse"
        or (storage == "auto" and len(ids) * labels.size > MAX_FIELD_VALUES)
    ):
        return initialize_sparse(grains, width)
    if len(ids) < 2 or len(ids) * labels.size > MAX_FIELD_VALUES:
        raise ValueError("needs >=2 grains within the dense field budget")
    fields = []
    unresolved = []
    for key in ids:
        mask = labels == key
        if ndi.label(mask)[1] != 1:
            raise ValueError(f"grain {key} contains disconnected components; split IDs explicitly")
        di = ndi.distance_transform_edt(mask) * grains.spacing
        do = ndi.distance_transform_edt(~mask) * grains.spacing
        distance = np.where(mask, di - 0.5 * grains.spacing, -do + 0.5 * grains.spacing)
        fields.append(profile(distance, width))
        if np.max(distance) < width / 2:
            unresolved.append(int(key))
    phi = np.stack(fields)
    phi /= phi.sum(axis=0)
    recovered = ids[phi.argmax(axis=0)]
    changes = []
    for k, key in enumerate(ids):
        before = int((labels == key).sum())
        changes.append(
            {
                "id": int(key),
                "pixelArea": before * grains.spacing**2,
                "diffuseArea": float(phi[k].sum() * grains.spacing**2),
                "relativeAreaChange": float(phi[k].sum() / before - 1),
                "centroidShiftPixels": float(
                    np.linalg.norm(
                        np.asarray(ndi.center_of_mass(phi[k]))
                        - np.asarray(ndi.center_of_mass(labels == key))
                    )
                ),
            }
        )
    return (
        phi,
        ids,
        {
            "sourceDigest": grains.digest,
            "width": width,
            "spacing": grains.spacing,
            "units": grains.units,
            "argmaxMismatchPixels": int((recovered != labels).sum()),
            "partitionResidual": float(np.max(abs(phi.sum(axis=0) - 1))),
            "unresolvedGrains": unresolved,
            "grainIdsPreserved": bool(np.array_equal(np.unique(recovered[recovered > 0]), ids)),
            "adjacencyPreserved": _adjacency(labels) == _adjacency(recovered),
            "maximumRelativeAreaChange": max(abs(c["relativeAreaChange"]) for c in changes),
            "maximumCentroidShiftPixels": max(c["centroidShiftPixels"] for c in changes),
            "areas": changes,
            "initializationTimeIsPhysical": False,
        },
    )


def _adjacency(labels):
    pairs = set()
    for a, b in [(labels[:-1], labels[1:]), (labels[:, :-1], labels[:, 1:])]:
        different = (a != b) & (a > 0) & (b > 0)
        pairs.update(tuple(sorted(p)) for p in zip(a[different], b[different], strict=True))
    return pairs


def initialize_sparse(grains, width):
    """Same distance profile as dense initialization, evaluated in finite-support boxes."""
    from .sparse import SparseFields

    labels = grains.labels
    domain = getattr(grains, "domain_mask", None)
    ids = np.unique(labels[labels > 0])
    if len(ids) < 2:
        raise ValueError("needs at least two grains")
    margin = int(np.ceil(width / (2 * grains.spacing))) + 2
    capacity = 4
    indices = np.full((*labels.shape, capacity), -1, np.int32)
    values = np.zeros(indices.shape, np.float64)
    counts = np.zeros(labels.shape, np.int32)
    patches, unresolved = [], []
    components = None if domain is None else ndi.label(domain)[0]
    if components is not None:
        for component_id, box in enumerate(ndi.find_objects(components), start=1):
            if box is not None and not np.all(components[box] == component_id):
                raise ValueError(
                    "label initialization currently supports rectangular material components only; "
                    "nonrectangular masks need a separately qualified distance reconstruction"
                )
    for index, key in enumerate(ids):
        yy, xx = np.nonzero(labels == key)
        y0, y1 = max(0, int(yy.min()) - margin), min(labels.shape[0], int(yy.max()) + margin + 1)
        x0, x1 = max(0, int(xx.min()) - margin), min(labels.shape[1], int(xx.max()) + margin + 1)
        mask = labels[y0:y1, x0:x1] == key
        if ndi.label(mask)[1] != 1:
            raise ValueError(f"grain {key} contains disconnected components; split IDs explicitly")
        # Excluded annotation/background pixels are not competing grain phases.
        local_domain = (
            np.ones(mask.shape, dtype=bool)
            if components is None
            else components[y0:y1, x0:x1] == components[yy[0], xx[0]]
        )
        inside = ndi.distance_transform_edt(mask | ~local_domain) * grains.spacing
        outside = ndi.distance_transform_edt(~mask) * grains.spacing
        distance = np.where(mask, inside - 0.5 * grains.spacing, -outside + 0.5 * grains.spacing)
        field = np.where(local_domain, profile(distance, width), 0)
        if distance[mask].max() < width / 2:
            unresolved.append(int(key))
        y, x = np.nonzero(field > 0)
        y, x = y + y0, x + x0
        if np.any(counts[y, x] == capacity):
            indices = np.pad(indices, ((0, 0), (0, 0), (0, capacity)), constant_values=-1)
            values = np.pad(values, ((0, 0), (0, 0), (0, capacity)))
            capacity *= 2
        indices[y, x, counts[y, x]] = index
        values[y, x, counts[y, x]] = field[y - y0, x - x0]
        counts[y, x] += 1
        patches.append((y, x, len(yy), np.array([yy.mean(), xx.mean()])))
    total = values.sum(axis=2)
    values /= np.where(total > 0, total, 1)[..., None]
    phi = SparseFields(indices, values, counts, len(ids), domain).validate()
    recovered = ids[np.maximum(phi.argmax(axis=0), 0)]
    if domain is not None:
        recovered = np.where(domain, recovered, 0)
    changes = []
    for index, key in enumerate(ids):
        y, x, before, centroid = patches[index]
        weights = np.sum(np.where(indices[y, x] == index, values[y, x], 0), axis=1)
        area = weights.sum()
        after_centroid = np.array([np.dot(y, weights), np.dot(x, weights)]) / area
        changes.append(
            {
                "id": int(key),
                "pixelArea": before * grains.spacing**2,
                "diffuseArea": float(area * grains.spacing**2),
                "relativeAreaChange": float(area / before - 1),
                "centroidShiftPixels": float(np.linalg.norm(after_centroid - centroid)),
            }
        )
    return (
        phi,
        ids,
        {
            "sourceDigest": grains.digest,
            "width": width,
            "spacing": grains.spacing,
            "units": grains.units,
            "argmaxMismatchPixels": int(np.sum(recovered != labels)),
            "partitionResidual": phi.residual(),
            "unresolvedGrains": unresolved,
            "grainIdsPreserved": bool(np.array_equal(np.unique(recovered[recovered > 0]), ids)),
            "adjacencyPreserved": _adjacency(labels) == _adjacency(recovered),
            "maximumRelativeAreaChange": max(abs(c["relativeAreaChange"]) for c in changes),
            "maximumCentroidShiftPixels": max(c["centroidShiftPixels"] for c in changes),
            "areas": changes,
            "initializationTimeIsPhysical": False,
            "fieldStorage": "local-exact-v1" if domain is None else "local-exact-masked-v1",
            "activeFieldValues": int(counts.sum()),
            "allocatedFieldValues": int(values.size),
            "sourceShape": list(labels.shape),
            "resampled": False,
            "excludedPixels": 0 if domain is None else int((~domain).sum()),
            "domainBoundary": "noflux",
        },
    )
