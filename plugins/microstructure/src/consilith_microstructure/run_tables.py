"""The tables of one evolution run, and the one rounding rule every reader of them shares.

Pure Python, no numpy: the report notebook, the training-set builder and the Adaption bridge
all read a run through ``RunTables`` and print numbers through ``sig``, so a number in a
completion is the number the report shows. The input is the ``trajectory`` block of the
``evolution_report`` result, which holds exactly what the evolution tables hold.
"""

from dataclasses import dataclass
from itertools import pairwise
from math import floor, isfinite, log10
from typing import Any

MAX_FRAMES = 61
MAX_GRAINS = 4096
DISCLAIMER = (
    "Hypothetical, dimensionless 2D grain-growth model; not calibrated to experiment and "
    "segmentation accuracy is not measured."
)
PARAMETER_KEYS = ("duration", "gamma", "mobility", "width")


def sig(value: float, digits: int = 4) -> str:
    """``value`` to ``digits`` significant digits, never in exponent form."""
    if value == 0:
        return "0"
    decimals = max(0, digits - 1 - floor(log10(abs(value))))
    text = f"{round(value, decimals):.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def percent(value: float) -> str:
    """A percentage with one decimal, as the report prints it."""
    return f"{abs(value):.1f}"


def _finite_list(value: Any, name: str, *, count: int | None = None) -> list[float]:
    if not isinstance(value, list) or (count is not None and len(value) != count):
        raise ValueError(f"{name} must be a list of {count if count is not None else 'numbers'}")
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float) or not isfinite(item):
            raise ValueError(f"{name} must hold finite numbers")
        out.append(float(item))
    return out


@dataclass(frozen=True)
class RunTables:
    """Validated, derived tables of one run. Built only by ``from_trajectory``."""

    times: list[float]
    energy: list[float]
    counts: list[int]
    grain_ids: list[int]
    areas: list[list[float]]  # areas[frame][grain]
    parameters: dict[str, float]

    @classmethod
    def from_trajectory(cls, trajectory: Any, parameters: Any = None) -> "RunTables":
        if not isinstance(trajectory, dict):
            raise ValueError("trajectory must be an object")
        times = _finite_list(trajectory.get("times"), "times")
        frames = len(times)
        if not 2 <= frames <= MAX_FRAMES:
            raise ValueError(f"a run has 2 to {MAX_FRAMES} recorded states, not {frames}")
        if any(b < a for a, b in pairwise(times)):
            raise ValueError("times must not decrease")
        energy = _finite_list(trajectory.get("energy"), "energy", count=frames)
        raw_counts = _finite_list(trajectory.get("visibleGrainCounts"), "visibleGrainCounts")
        if len(raw_counts) != frames or any(c != int(c) or c < 0 for c in raw_counts):
            raise ValueError("visibleGrainCounts must be one whole number per state")
        raw_ids = _finite_list(trajectory.get("grainIds"), "grainIds")
        if not 1 <= len(raw_ids) <= MAX_GRAINS or any(i != int(i) for i in raw_ids):
            raise ValueError("grainIds must be a non-empty list of whole numbers")
        ids = [int(i) for i in raw_ids]
        if len(set(ids)) != len(ids):
            raise ValueError("grainIds must be distinct")
        raw_areas = trajectory.get("diffuseAreas")
        if not isinstance(raw_areas, list) or len(raw_areas) != frames:
            raise ValueError("diffuseAreas must hold one row per state")
        areas = [_finite_list(row, "diffuseAreas", count=len(ids)) for row in raw_areas]
        if any(a < 0 for row in areas for a in row):
            raise ValueError("diffuseAreas must not be negative")
        clean: dict[str, float] = {}
        if parameters is not None:
            if not isinstance(parameters, dict):
                raise ValueError("parameters must be an object")
            for key in PARAMETER_KEYS:
                if key in parameters:
                    clean[key] = _finite_list([parameters[key]], key)[0]
        return cls(times, energy, [int(c) for c in raw_counts], ids, areas, clean)

    # -- derived quantities: the single definition each reader uses ----------------------------
    @property
    def frames(self) -> int:
        return len(self.times)

    def total_area(self, frame: int) -> float:
        return sum(self.areas[frame])

    def mean_area(self, frame: int) -> float:
        """Total diffuse area over the visible grain count of that state (pixel squared)."""
        return self.total_area(frame) / max(1, self.counts[frame])

    def fractional_loss(self, grain: int) -> float:
        """Share of its first-state diffuse area a grain has lost by the last state."""
        first, last = self.areas[0][grain], self.areas[-1][grain]
        return (first - last) / first if first > 0 else 0.0

    def fastest_shrinking(self) -> int:
        """Index of the grain with the largest fractional loss; the first of equals."""
        losses = [self.fractional_loss(g) for g in range(len(self.grain_ids))]
        return max(range(len(losses)), key=lambda g: (losses[g], -g))

    def slowest_shrinking(self) -> int:
        """Index of the grain with the smallest fractional loss; the first of equals."""
        losses = [self.fractional_loss(g) for g in range(len(self.grain_ids))]
        return min(range(len(losses)), key=lambda g: (losses[g], g))

    def vanished(self) -> list[int]:
        """Grain ids whose diffuse area is zero in the last state and positive in the first."""
        return [
            gid
            for g, gid in enumerate(self.grain_ids)
            if self.areas[0][g] > 0 and self.areas[-1][g] == 0
        ]

    # -- the tables the report prints ---------------------------------------------------------
    def history_table(self) -> dict[str, Any]:
        return {
            "columns": [
                "Time (dimensionless)",
                "Visible grains",
                "Free energy (dimensionless)",
                "Mean grain area (px squared)",
            ],
            "rows": [
                [
                    sig(self.times[i]),
                    str(self.counts[i]),
                    sig(self.energy[i]),
                    sig(self.mean_area(i)),
                ]
                for i in range(self.frames)
            ],
        }

    def shrinking_table(self, limit: int = 8) -> dict[str, Any]:
        """The grains that lost the largest share of their area, largest first."""
        order = sorted(range(len(self.grain_ids)), key=lambda g: (-self.fractional_loss(g), g))
        return {
            "columns": [
                "Grain",
                "First area (px squared)",
                "Last area (px squared)",
                "Area lost (%)",
            ],
            "rows": [
                [
                    str(self.grain_ids[g]),
                    sig(self.areas[0][g]),
                    sig(self.areas[-1][g]),
                    percent(100 * self.fractional_loss(g)),
                ]
                for g in order[:limit]
            ],
        }
