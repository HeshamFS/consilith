"""Small variational 2D multiphase obstacle solver. See MODEL.md for conventions."""

from dataclasses import dataclass

import numpy as np

from .data import MAX_FIELD_VALUES, positive
from .sparse import SparseFields, sparse_energy, sparse_step


@dataclass(frozen=True)
class Parameters:
    spacing: float
    width: float
    gamma: float = 1.0
    mobility: float = 1.0
    boundary: str = "noflux"
    units: str = "dimensionless"

    def __post_init__(self):
        for name in ("spacing", "width", "gamma"):
            positive(getattr(self, name), name)
        positive(self.mobility, "mobility", True)
        if self.width < 4 * self.spacing or self.boundary not in {"noflux", "periodic"}:
            raise ValueError("unsupported boundary or underresolved interface")
        if self.units not in {"SI", "dimensionless"}:
            raise ValueError("units must be SI or dimensionless")

    @property
    def kappa(self):
        return 4 * self.gamma * self.width / np.pi**2

    @property
    def barrier(self):
        return 4 * self.gamma / self.width

    @property
    def rate(self):
        return np.pi**2 * self.mobility / (4 * self.width)

    @property
    def dt_limit(self):
        return (
            np.inf
            if self.mobility == 0
            else 0.8 / (self.rate * (8 * self.kappa / self.spacing**2 + self.barrier))
        )


def validate_fields(phi):
    if isinstance(phi, SparseFields):
        return phi.validate()
    a = np.asarray(phi, dtype=np.float64)
    if a.ndim != 3 or a.shape[0] < 2 or min(a.shape[1:]) < 2 or a.size > MAX_FIELD_VALUES:
        raise ValueError("fields must be bounded [grain,y,x] arrays")
    if (
        not np.isfinite(a).all()
        or a.min() < -1e-12
        or a.max() > 1 + 1e-12
        or np.max(abs(a.sum(axis=0) - 1)) > 1e-10
    ):
        raise ValueError("invalid finite simplex fields")
    return a


def laplacian(a, p):
    if p.boundary == "periodic":
        return sum(np.roll(a, d, axis=ax) - a for ax in (1, 2) for d in (-1, 1)) / p.spacing**2
    out = np.zeros_like(a)
    dy = np.diff(a, axis=1)
    dx = np.diff(a, axis=2)
    out[:, :-1] += dy
    out[:, 1:] -= dy
    out[:, :, :-1] += dx
    out[:, :, 1:] -= dx
    return out / p.spacing**2


def energy(a, p):
    if isinstance(a, SparseFields):
        return sparse_energy(a, p)
    grad = 0.0
    for axis in (1, 2):
        d = np.roll(a, -1, axis=axis) - a if p.boundary == "periodic" else np.diff(a, axis=axis)
        grad += float(np.sum(d * d))
    return 0.5 * p.kappa * grad + 0.5 * p.barrier * p.spacing**2 * float(np.sum(a * (1 - a)))


def gradient(a, p):
    return -p.kappa * laplacian(a, p) + p.barrier * (0.5 - a)


def simplex(a):
    if len(a) == 2:
        first = np.clip((1 + a[0] - a[1]) / 2, 0, 1)
        return np.stack([first, 1 - first])
    # Euclidean projection via sorted active-set threshold, independently per cell.
    u = np.sort(a, axis=0)[::-1]
    sums = np.cumsum(u, axis=0) - 1
    ranks = np.arange(1, len(u) + 1)[:, None, None]
    active = u - sums / ranks > 0
    rho = active.sum(axis=0) - 1
    theta = np.take_along_axis(sums, rho[None], axis=0)[0] / (rho + 1)
    return np.maximum(a - theta, 0)


def evolve(
    phi, p: Parameters, duration, dt=None, *, max_steps=20000, record_every=20, observer=None
):
    a = validate_fields(phi).copy()
    duration = positive(duration, "duration", True)
    if (
        type(max_steps) is not int
        or not 1 <= max_steps <= 20000
        or type(record_every) is not int
        or record_every < 1
    ):
        raise ValueError("invalid step budget or recording cadence")
    step = p.dt_limit if dt is None else positive(dt, "dt")
    if step > p.dt_limit * (1 + 1e-12):
        raise ValueError("requested timestep exceeds the energy-stability bound")
    if observer is not None:
        observer(a, 0.0, 0)
    if p.mobility == 0 or duration == 0:
        return a, {
            "time": duration,
            "steps": 0,
            "energy": [energy(a, p)],
            "times": [0.0],
            "stoppingReason": "zero_mobility" if p.mobility == 0 else "zero_duration",
        }
    if np.ceil(duration / step) > max_steps:
        raise ValueError("requested duration exceeds step budget; use bounded chunks")
    t = 0.0
    energies = [energy(a, p)]
    times = [t]
    before = energies[0]
    n = 0
    scratch = None
    while t < duration:
        h = min(step, duration - t)
        if h <= np.finfo(float).eps * max(duration, step):
            break
        candidate = (
            sparse_step(a, p, h, scratch)
            if isinstance(a, SparseFields)
            else simplex(a - h * p.rate * gradient(a, p))
        )
        after = energy(candidate, p)
        if not np.isfinite(after) or after > before + 1e-12 * max(
            abs(before), np.finfo(float).tiny
        ):
            raise RuntimeError("discrete energy increased; no step was accepted")
        scratch = a if isinstance(a, SparseFields) else None
        a = candidate
        before = after
        t += h
        n += 1
        if n % record_every == 0 or t >= duration:
            energies.append(after)
            times.append(t)
            if observer is not None:
                observer(a, t, n)
    validate_fields(a)
    return a, {
        "time": t,
        "steps": n,
        "energy": energies,
        "times": times,
        "stoppingReason": "completed",
        "dt": step,
        "units": p.units,
        "partitionResidual": a.residual()
        if isinstance(a, SparseFields)
        else float(abs(a.sum(axis=0) - 1).max()),
    }
