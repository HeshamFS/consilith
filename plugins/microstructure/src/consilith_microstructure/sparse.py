"""Exact local storage for compact-support multiphase obstacle fields.

A projected step only needs phases present at the cell or a stencil neighbour.
After subtracting the common -dt*L*W/2, absent candidates are zero. The sum of
all candidates is 1+dt*L*W, so the simplex threshold is positive. Therefore a
phase absent from that union cannot enter the projected state. No small-value
cutoff, grain truncation, or spatial resampling is used.
"""

import os
from dataclasses import dataclass

import numpy as np
from numba import get_num_threads, njit, prange, set_num_threads

# Keep standalone calls bounded too; explicit deployment configuration takes precedence.
if "NUMBA_NUM_THREADS" not in os.environ:
    set_num_threads(min(4, get_num_threads()))


@dataclass
class SparseFields:
    indices: np.ndarray
    values: np.ndarray
    counts: np.ndarray
    grain_count: int
    domain_mask: np.ndarray | None = None

    @property
    def shape(self):
        return (self.grain_count, *self.counts.shape)

    def copy(self):
        return SparseFields(
            self.indices.copy(),
            self.values.copy(),
            self.counts.copy(),
            self.grain_count,
            None if self.domain_mask is None else self.domain_mask.copy(),
        )

    def argmax(self, axis=0):
        if axis != 0:
            raise ValueError("sparse argmax supports grain axis only")
        maximum = self.values.max(axis=2)
        return np.min(
            np.where(self.values == maximum[..., None], self.indices, self.grain_count), axis=2
        )

    def to_dense(self):
        a = np.zeros(self.shape)
        y, x, k = np.nonzero(self.values > 0)
        a[self.indices[y, x, k], y, x] = self.values[y, x, k]
        return a

    def areas(self):
        valid = self.indices >= 0
        return np.bincount(
            self.indices[valid], weights=self.values[valid], minlength=self.grain_count
        )

    def density(self):
        density = 1 - np.sum(self.values * self.values, axis=2)
        return density if self.domain_mask is None else np.where(self.domain_mask, density, 0)

    def residual(self):
        target = 1 if self.domain_mask is None else self.domain_mask
        return float(np.max(np.abs(self.values.sum(axis=2) - target)))

    def validate(self):
        if (
            self.indices.ndim != 3
            or self.values.ndim != 3
            or self.counts.ndim != 2
            or self.indices.shape != self.values.shape
            or self.values.shape[:2] != self.counts.shape
        ):
            raise ValueError("invalid sparse shapes")
        if (
            self.indices.dtype != np.int32
            or self.counts.dtype != np.int32
            or self.values.dtype != np.float64
        ):
            raise ValueError("sparse fields require int32 indices/counts and float64 values")
        if (
            type(self.grain_count) is not int
            or self.grain_count < 2
            or self.grain_count > 4_194_304
            or min(self.counts.shape) < 2
            or self.counts.size > 4_194_304
            or not 1 <= self.values.shape[2] <= self.grain_count * 2
        ):
            raise ValueError("invalid sparse field dimensions or grain count")
        if self.domain_mask is not None and (
            not isinstance(self.domain_mask, np.ndarray)
            or self.domain_mask.dtype != np.bool_
            or self.domain_mask.shape != self.counts.shape
            or not self.domain_mask.any()
        ):
            raise ValueError("invalid explicit domain mask")
        if (
            not np.isfinite(self.values).all()
            or self.values.min() < 0
            or self.values.max() > 1 + 1e-12
            or self.residual() > 1e-10
        ):
            raise ValueError("invalid finite simplex fields")
        if (
            np.any((self.values > 0) != (self.indices >= 0))
            or np.any(self.indices >= self.grain_count)
            or np.any(self.indices < -1)
        ):
            raise ValueError("invalid sparse phase indices")
        domain = (
            np.ones(self.counts.shape, dtype=bool) if self.domain_mask is None else self.domain_mask
        )
        if (
            np.any(self.counts[domain] < 1)
            or np.any(self.counts > self.values.shape[2])
            or np.any(self.counts[~domain] != 0)
        ):
            raise ValueError("sparse counts exceed slot capacity")
        active = np.arange(self.values.shape[2])[None, None, :] < self.counts[..., None]
        if np.any(active != (self.values > 0)):
            raise ValueError("sparse active slots must be contiguous and match counts")
        ordered = np.sort(self.indices, axis=2)
        if np.any((np.diff(ordered, axis=2) == 0) & (ordered[..., 1:] >= 0)):
            raise ValueError("duplicate sparse phase indices")
        return self

    @classmethod
    def from_dense(cls, a, capacity=None, *, domain_mask=None):
        a = np.asarray(a, dtype=np.float64)
        if a.ndim != 3 or a.shape[0] < 2 or min(a.shape[1:]) < 2:
            raise ValueError("invalid dense field shape")
        if domain_mask is not None and (
            not isinstance(domain_mask, np.ndarray)
            or domain_mask.dtype != np.bool_
            or domain_mask.shape != a.shape[1:]
            or not domain_mask.any()
        ):
            raise ValueError("invalid explicit domain mask")
        target = 1 if domain_mask is None else domain_mask
        if (
            not np.isfinite(a).all()
            or a.min() < 0
            or a.max() > 1 + 1e-12
            or np.max(np.abs(a.sum(axis=0) - target)) > 1e-10
        ):
            raise ValueError("invalid finite simplex fields")
        if capacity is not None and (type(capacity) is not int or capacity < 1):
            raise ValueError("capacity must be a positive integer")
        count = np.sum(a > 0, axis=0).astype(np.int32)
        cap = max(2, int(count.max())) if capacity is None else max(capacity, int(count.max()))
        indices = np.full((*a.shape[1:], cap), -1, np.int32)
        values = np.zeros(indices.shape, np.float64)
        slots = np.zeros(a.shape[1:], np.int32)
        for grain, plane in enumerate(a):
            y, x = np.nonzero(plane > 0)
            indices[y, x, slots[y, x]] = grain
            values[y, x, slots[y, x]] = plane[y, x]
            slots[y, x] += 1
        return cls(indices, values, count, len(a), domain_mask).validate()


def arrays_for_fields(prefix, fields):
    if isinstance(fields, SparseFields):
        return {
            f"{prefix}_indices": fields.indices,
            f"{prefix}_values": fields.values,
            f"{prefix}_counts": fields.counts,
            f"{prefix}_grain_count": np.array(fields.grain_count),
            f"{prefix}_storage": np.array(
                "local-exact-v1" if fields.domain_mask is None else "local-exact-masked-v1"
            ),
            **({} if fields.domain_mask is None else {f"{prefix}_domain_mask": fields.domain_mask}),
        }
    return {prefix: fields}


@njit(cache=True, parallel=True)
def _step(indices, values, counts, c, b, periodic, out_indices, out_values, out_counts):
    height, width, capacity = values.shape
    # Per-row scratch is reused across cells; rows run independently.
    for row in prange(height):
        y = np.int64(row)
        keys = np.empty(capacity * 5, np.int32)
        candidates = np.empty(capacity * 5, np.float64)
        sorted_values = np.empty(capacity * 5, np.float64)
        for x in range(width):
            if counts[y, x] == 0:
                out_counts[y, x] = 0
                continue
            ym = y - 1 if y > 0 else (height - 1 if periodic else y)
            yp = y + 1 if y + 1 < height else (0 if periodic else y)
            xm = x - 1 if x > 0 else (width - 1 if periodic else x)
            xp = x + 1 if x + 1 < width else (0 if periodic else x)
            if counts[ym, x] == 0:
                ym = y
            if counts[yp, x] == 0:
                yp = y
            if counts[y, xm] == 0:
                xm = x
            if counts[y, xp] == 0:
                xp = x
            first = indices[y, x, 0]
            if (
                counts[y, x] == 1
                and counts[ym, x] == 1
                and counts[yp, x] == 1
                and counts[y, xm] == 1
                and counts[y, xp] == 1
                and indices[ym, x, 0] == first
                and indices[yp, x, 0] == first
                and indices[y, xm, 0] == first
                and indices[y, xp, 0] == first
            ):
                out_indices[y, x, 0] = first
                out_values[y, x, 0] = 1.0
                out_counts[y, x] = 1
                continue
            n = counts[y, x]
            for k in range(n):
                keys[k] = indices[y, x, k]
                candidates[k] = (1 + b - 4 * c) * values[y, x, k]
            for yy, xx in ((ym, x), (yp, x), (y, xm), (y, xp)):
                for k in range(counts[yy, xx]):
                    key = indices[yy, xx, k]
                    found = -1
                    for j in range(n):
                        if keys[j] == key:
                            found = j
                            break
                    if found < 0:
                        found = n
                        keys[n] = key
                        candidates[n] = 0.0
                        n += 1
                    candidates[found] += c * values[yy, xx, k]
            # Insertion sort is faster for these small exact active sets.
            for k in range(n):
                v = candidates[k]
                j = k - 1
                while j >= 0 and sorted_values[j] < v:
                    sorted_values[j + 1] = sorted_values[j]
                    j -= 1
                sorted_values[j + 1] = v
            total = 0.0
            theta = 0.0
            for k in range(n):
                total += sorted_values[k]
                trial = (total - 1) / (k + 1)
                if sorted_values[k] > trial:
                    theta = trial
            used = 0
            for k in range(n):
                v = candidates[k] - theta
                if v > 0:
                    if used < capacity:
                        out_indices[y, x, used] = keys[k]
                        out_values[y, x, used] = v
                    used += 1
            out_counts[y, x] = used
    return out_counts.max()


@njit(cache=True, parallel=True)
def _energy(indices, values, counts, kappa, barrier_area, periodic):
    height, width, _ = values.shape
    grad = 0.0
    potential = 0.0
    for row in prange(height):
        y = np.int64(row)
        for x in range(width):
            if counts[y, x] == 0:
                continue
            for k in range(counts[y, x]):
                v = values[y, x, k]
                potential += v * (1 - v)
            for axis in range(2):
                yy, xx = (y + 1, x) if axis == 0 else (y, x + 1)
                if yy == height or xx == width:
                    if not periodic:
                        continue
                    yy %= height
                    xx %= width
                if counts[yy, xx] == 0:
                    continue
                if (
                    counts[y, x] == 1
                    and counts[yy, xx] == 1
                    and indices[y, x, 0] == indices[yy, xx, 0]
                ):
                    continue
                for k in range(counts[y, x]):
                    v = values[y, x, k]
                    key = indices[y, x, k]
                    other = 0.0
                    for j in range(counts[yy, xx]):
                        if indices[yy, xx, j] == key:
                            other = values[yy, xx, j]
                            break
                    grad += (v - other) ** 2
                for j in range(counts[yy, xx]):
                    found = False
                    for k in range(counts[y, x]):
                        if indices[y, x, k] == indices[yy, xx, j]:
                            found = True
                            break
                    if not found:
                        grad += values[yy, xx, j] ** 2
    return 0.5 * (kappa * grad + barrier_area * potential)


def sparse_energy(a, p):
    return _energy(
        a.indices, a.values, a.counts, p.kappa, p.barrier * p.spacing**2, p.boundary == "periodic"
    )


def sparse_step(a, p, h, output=None):
    capacity = a.values.shape[2]
    if output is None or output.values.shape != a.values.shape:
        output = SparseFields(
            np.full_like(a.indices, -1),
            np.zeros_like(a.values),
            np.zeros_like(a.counts),
            a.grain_count,
            a.domain_mask,
        )
    else:
        output.indices.fill(-1)
        output.values.fill(0)
    used = _step(
        a.indices,
        a.values,
        a.counts,
        h * p.rate * p.kappa / p.spacing**2,
        h * p.rate * p.barrier,
        p.boundary == "periodic",
        output.indices,
        output.values,
        output.counts,
    )
    if used > capacity:
        # Retry the entire step with larger capacity. The incomplete output is never accepted.
        extra = max(used, capacity * 2) - capacity
        enlarged = SparseFields(
            np.pad(a.indices, ((0, 0), (0, 0), (0, extra)), constant_values=-1),
            np.pad(a.values, ((0, 0), (0, 0), (0, extra))),
            a.counts,
            a.grain_count,
            a.domain_mask,
        )
        return sparse_step(enlarged, p, h)
    return output
