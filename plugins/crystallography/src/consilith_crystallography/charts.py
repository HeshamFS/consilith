"""Chart artifacts of a polyhedra report: coordination numbers and bond-length distributions.

Pure Python on the report as plain JSON (``PolyhedraReport.model_dump(mode="json")``), with no
third-party import, so the same builders feed the room's chart artifacts and the marimo report.
Every number is a count or a distance the analysis already computed: the counts are of the
polyhedra and ligands in the report, and the bins only group those distances. Nothing is
fitted, smoothed or inferred.

An artifact is ``{"kind": "chart", "chart": <ChartData>}``; the shape of ``ChartData`` is the
web app's ``A/types.ts``.
"""

from __future__ import annotations

from collections import Counter
from math import ceil, floor
from typing import Any

MAX_SERIES = 8
MAX_BINS = 16
BIN_WIDTHS = (0.005, 0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0)
PAIR_JOIN = "–"  # "Ti–O"


def _coordination(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The polyhedra the analysis built around a centre, without element-shell families."""
    kinds = {
        t["type_id"]: t.get("kind", "coordination") for t in report.get("polyhedron_types", [])
    }
    return [p for p in report.get("polyhedra", []) if kinds.get(p["type_id"]) != "element_shell"]


def _title(report: dict[str, Any], what: str) -> str:
    formula = report.get("structure", {}).get("reduced_formula", "")
    return f"{what}, {formula}" if formula else what


def coordination_histogram(report: dict[str, Any]) -> dict[str, Any] | None:
    """Polyhedra in the cell by coordination number, one series per centre element."""
    polyhedra = _coordination(report)
    if not polyhedra:
        return None
    counts: dict[str, Counter[int]] = {}
    for p in polyhedra:
        counts.setdefault(p["center_element"], Counter())[
            int(p["metrics"]["coordination_number"])
        ] += 1
    numbers = sorted({n for c in counts.values() for n in c})
    series = [
        {
            "name": element,
            "x": [str(n) for n in numbers],
            "y": [counts[element][n] for n in numbers],
        }
        for element in sorted(counts)
    ]
    return {
        "kind": "chart",
        "chart": {
            "kind": "bar",
            "title": _title(report, "Coordination numbers"),
            "xLabel": "Coordination number",
            "yLabel": "Polyhedra in the cell",
            "xUnit": "",
            "yUnit": "",
            "series": series,
        },
    }


def _edges(low: float, high: float) -> tuple[float, float, int]:
    """Start, width and count of equal bins that cover [low, high] with round edges."""
    span = high - low
    if span < 1e-9:
        return round(low - 0.005, 6), 0.01, 1
    width = next((w for w in BIN_WIDTHS if span / w <= MAX_BINS - 1), BIN_WIDTHS[-1])
    start = floor(low / width + 1e-9) * width
    count = max(1, ceil((high - start) / width - 1e-9))
    if start + count * width <= high + 1e-12:
        count += 1  # the largest distance sits on an edge: it belongs to the last bin
    return round(start, 6), width, count


def _label(value: float, width: float) -> str:
    decimals = 3 if width < 0.1 else 2 if width < 1 else 1
    return f"{value:.{decimals}f}"


def bond_length_distribution(report: dict[str, Any]) -> dict[str, Any] | None:
    """Bond lengths of the cell as a histogram, one series per cation-anion pair.

    All pairs share the same bins, so their bars line up. The x labels are bin centres in
    angstrom; the y values are numbers of bonds in the cell.
    """
    oxidation = report.get("settings", {}).get("oxidation_states") or {}
    polyhedra = _coordination(report)
    # A bond is seen from both ends; count it once, from the cation, when charges are known.
    from_cations = [p for p in polyhedra if (oxidation.get(p["center_element"]) or 0) > 0]
    bonds: dict[tuple[str, str], list[float]] = {}
    for p in from_cations or polyhedra:
        for ligand in p["ligands"]:
            bonds.setdefault((p["center_element"], ligand["element"]), []).append(
                float(ligand["distance"])
            )
    if not bonds:
        return None
    ranked = sorted(bonds, key=lambda pair: (-len(bonds[pair]), pair))
    shown = sorted(ranked[:MAX_SERIES])
    distances = [d for pair in shown for d in bonds[pair]]
    start, width, count = _edges(min(distances), max(distances))
    centres = [_label(start + (i + 0.5) * width, width) for i in range(count)]
    series = []
    for pair in shown:
        bins = [0] * count
        for d in bonds[pair]:
            bins[min(count - 1, max(0, floor((d - start) / width + 1e-9)))] += 1
        series.append({"name": PAIR_JOIN.join(pair), "x": centres, "y": bins})
    chart: dict[str, Any] = {
        "kind": "bar",
        "title": _title(report, "Bond lengths"),
        "xLabel": "Bond length, bin centre (Å)",
        "yLabel": "Bonds in the cell",
        "xUnit": "",
        "yUnit": "",
        "series": series,
    }
    if len(ranked) > len(shown):
        chart["omittedSeries"] = len(ranked) - len(shown)
    return {"kind": "chart", "chart": chart}
