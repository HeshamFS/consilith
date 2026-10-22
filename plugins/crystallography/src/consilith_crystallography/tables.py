"""Flat CSV tables of a polyhedra report.

* ``polyhedra`` – one row per polyhedron in the cell (every symmetry-equivalent copy)
* ``types``     – one row per symmetry-distinct polyhedron
* ``sites``     – one row per Wyckoff site: coordination environment and whether it forms a polyhedron
* ``bonds``     – one row per centre–ligand bond

Units: Å, Å^3, degrees, deg^2, valence units.
"""

from __future__ import annotations

import csv
import io
import math

from .schema import PolyhedraReport

TABLES = ("polyhedra", "types", "sites", "bonds")

METRIC_COLUMNS = [
    ("coordination_number", "CN"),
    ("shape", "shape"),
    ("average_bond_length", "average_bond_length_A"),
    ("min_bond_length", "min_bond_length_A"),
    ("max_bond_length", "max_bond_length_A"),
    ("polyhedral_volume", "polyhedral_volume_A3"),
    ("distortion_index", "distortion_index"),
    ("effective_coordination_number", "effective_coordination_number"),
    ("weighted_average_bond_length", "ECoN_weighted_bond_length_A"),
    ("quadratic_elongation", "quadratic_elongation"),
    ("bond_angle_variance", "bond_angle_variance_deg2"),
    ("bond_valence_sum", "bond_valence_sum_vu"),
    ("center_offset", "off_centring_A"),
]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return v


def _metrics(m) -> dict:
    d = m.model_dump()
    return {col: _fmt(d[key]) for key, col in METRIC_COLUMNS}


def _sharing(connections, same_type: str | None = None) -> dict:
    out = {"corner_shared": 0, "edge_shared": 0, "face_shared": 0}
    angles = []
    for c in connections:
        if same_type and c.neighbor_type_id != same_type:
            continue
        out[f"{c.sharing}_shared"] += 1
        if c.sharing == "corner":
            angles.extend(c.bridging_angles)
    out["mean_corner_bridging_angle_deg"] = _fmt(sum(angles) / len(angles)) if angles else ""
    return out


def rows(report: PolyhedraReport, table: str = "polyhedra") -> list[dict]:
    st = report.structure
    common = {
        "structure": st.source.id or st.reduced_formula,
        "formula": st.reduced_formula,
        "space_group": f"{st.space_group.symbol} ({st.space_group.number})",
    }
    oxi = report.settings.oxidation_states or {}
    kinds = {t.type_id: t.kind for t in report.polyhedron_types}
    site_sym = {s.index: s.site_symmetry for s in st.sites}

    if table == "polyhedra":
        out = []
        for p in report.polyhedra:
            out.append(
                {
                    **common,
                    "type_id": p.type_id,
                    "kind": kinds.get(p.type_id, ""),
                    "center": p.center_label,
                    "element": p.center_element,
                    "oxidation_state": _fmt(oxi.get(p.center_element)),
                    "wyckoff": p.center_wyckoff,
                    "site_symmetry": site_sym.get(p.center_index, ""),
                    "x": _fmt(p.center_frac[0]),
                    "y": _fmt(p.center_frac[1]),
                    "z": _fmt(p.center_frac[2]),
                    "ligands": "".join(sorted({l.element for l in p.ligands})),
                    **_metrics(p.metrics),
                    **_sharing(p.connections, p.type_id),
                    "bond_lengths_A": ";".join(f"{l.label}:{l.distance:.5f}" for l in p.ligands),
                }
            )
        return out

    if table == "types":
        return [
            {
                **common,
                "type_id": t.type_id,
                "kind": t.kind,
                "center": t.center_label,
                "element": t.center_element,
                "wyckoff": t.wyckoff,
                "site_symmetry": t.site_symmetry,
                "ligand_formula": t.ligand_formula,
                "n_in_cell": t.n_instances,
                **_metrics(t.metrics),
                "corner_shared_per_polyhedron": _fmt(t.connectivity.get("corner", 0.0)),
                "edge_shared_per_polyhedron": _fmt(t.connectivity.get("edge", 0.0)),
                "face_shared_per_polyhedron": _fmt(t.connectivity.get("face", 0.0)),
                "mean_corner_bridging_angle_deg": _fmt(t.mean_bridging_angle),
            }
            for t in report.polyhedron_types
        ]

    if table == "sites":
        return [
            {
                **common,
                "site": e.site_label,
                "element": e.element,
                "oxidation_state": _fmt(e.oxidation_state),
                "wyckoff": e.wyckoff,
                "site_symmetry": e.site_symmetry,
                "multiplicity_in_cell": e.multiplicity_in_cell,
                "x": _fmt(e.representative_frac[0]),
                "y": _fmt(e.representative_frac[1]),
                "z": _fmt(e.representative_frac[2]),
                "CN": e.coordination_number,
                "ligand_formula": e.ligand_formula,
                "shape": e.shape,
                "forms_polyhedron": e.forms_polyhedron,
                "reason": e.reason,
                "symmetry_consistent": _fmt(e.symmetry_consistent),
                "shannon_cn": _fmt(e.shannon.shannon_cn) if e.shannon else "",
                "shannon_consistent": _fmt(e.shannon.shannon_consistent) if e.shannon else "",
                "center_radius_A": _fmt(e.shannon.center_radius) if e.shannon else "",
                "ligand_radius_A": _fmt(e.shannon.ligand_radius) if e.shannon else "",
                "expected_bond_length_A": _fmt(e.shannon.expected_bond_length) if e.shannon else "",
                "radius_ratio": _fmt(e.shannon.radius_ratio) if e.shannon else "",
                "pauling_cn": _fmt(e.shannon.pauling_cn) if e.shannon else "",
                "ionic_radius_A": _fmt(e.ionic_radius),
                "ionic_radius_cn": _fmt(e.ionic_radius_cn),
                "polyhedron_types": ";".join(e.type_ids),
            }
            for e in report.site_environments
        ]

    if table == "bonds":
        out = []
        for p in report.polyhedra:
            for l in p.ligands:
                out.append(
                    {
                        **common,
                        "type_id": p.type_id,
                        "center": p.center_label,
                        "center_wyckoff": p.center_wyckoff,
                        "ligand": l.label,
                        "ligand_element": l.element,
                        "ligand_image": " ".join(str(i) for i in l.image),
                        "distance_A": f"{l.distance:.5f}",
                    }
                )
        return out

    if table == "checks":
        return [dict(zip(CHECK_COLUMNS, row, strict=True)) for row in check_rows(report)]

    raise ValueError(f"table must be one of {', '.join((*TABLES, 'checks'))}")


def to_csv(report: PolyhedraReport, table: str = "polyhedra") -> str:
    data = rows(report, table)
    buf = io.StringIO()
    if data:
        w = csv.DictWriter(buf, fieldnames=list(data[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(data)
    return buf.getvalue()


CHECK_COLUMNS = ("check", "site", "detail", "result")


def _js(x: object) -> str:
    """A number the way JavaScript prints it in a template string: 2.0 is "2"."""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _f(x: object, n: int = 4) -> str:
    """viewer.html ``fmt``: a fixed number of decimals, an en dash for a missing value."""
    return "–" if x is None else f"{float(x):.{n}f}"


def _verdict(ok: bool) -> str:
    return "pass" if ok else "fail"


def check_rows(report: PolyhedraReport) -> list[tuple[str, str, str, str]]:
    """The crystal-chemical checks as (check, site, detail, result) rows.

    Mirrors ``checksHtml`` in viewer.html: a tick is "pass", a cross "fail", a warning sign "warn"
    and a row without a mark "info".
    """
    cc = report.crystal_chemistry
    out: list[tuple[str, str, str, str]] = []
    for r in cc.pauling_rule_1_radius_ratio:
        detail = (
            f"r+/r− {_f(r['radius_ratio'], 3)} → CN {_js(r['predicted_cn'])}, "
            f"observed {_js(r['observed_cn'])}"
        )
        out.append(("Pauling 1", r["site"], detail, _verdict(bool(r["agrees"]))))
    for r in cc.pauling_rule_2_electrostatic_valence:
        detail = (
            f"Σ z/CN = {_f(r['sum_of_electrostatic_bond_strengths'], 3)} "
            f"vs {_js(r['anion_charge'])}"
        )
        out.append(("Pauling 2", r["site"], detail, _verdict(bool(r["satisfied"]))))
    for pair, links in cc.pauling_rule_3_polyhedron_sharing.items():
        shared = ", ".join(f"{v} {k}" for k, v in links.items() if v)
        out.append(("Pauling 3", pair, f"{shared} links in cell", "info"))
    for r in cc.pauling_rule_4_high_valence_sharing:
        detail = f"{r['sharing']}-sharing at {_f(r['cation_distance'], 3)} Å"
        out.append(("Pauling 4", r["pair"], detail, "warn"))
    parsimony = ", ".join(
        f"{k}: {v} environment{'s' if v > 1 else ''}"
        for k, v in cc.pauling_rule_5_parsimony.items()
    )
    out.append(("Pauling 5", "", parsimony, "info"))
    for r in cc.bond_valence_sums:
        detail = f"{_f(r['bvs'], 3)} vs {_js(r['expected'])}"
        near = abs(r["deviation"]) <= 0.2 * r["expected"]
        out.append(("BVS", r["site"], detail, "pass" if near else "warn"))
    if cc.global_instability_index is not None:
        gii = cc.global_instability_index
        out.append(("GII", "", f"{_f(gii, 3)} v.u.", "pass" if gii <= 0.2 else "warn"))
    if cc.bond_count_balance is not None:
        b = cc.bond_count_balance
        detail = f"cations {_js(b['cation_side'])}, anions {_js(b['anion_side'])}"
        out.append(("Bond count", "", detail, _verdict(bool(b["balanced"]))))
    out.append(
        (
            "Site symmetry",
            "",
            "every polyhedron invariant under its site group",
            _verdict(cc.site_symmetry_consistent),
        )
    )
    return out


# The typed table artifact: the same rows as the CSV tables, with numbers as numbers and the
# unit of each column named once. ``labels`` and ``units`` are display data for the interface;
# ``columns`` and ``rows`` keep the shape older readers expect.
LABELS = {
    "type_id": "Type",
    "kind": "Kind",
    "center": "Centre",
    "element": "Element",
    "wyckoff": "Wyckoff",
    "site_symmetry": "Site symmetry",
    "ligand_formula": "Ligands",
    "n_in_cell": "In cell",
    "CN": "CN",
    "shape": "Shape",
    "average_bond_length_A": "Mean bond length",
    "min_bond_length_A": "Shortest bond",
    "max_bond_length_A": "Longest bond",
    "polyhedral_volume_A3": "Volume",
    "distortion_index": "Distortion index",
    "effective_coordination_number": "ECoN",
    "ECoN_weighted_bond_length_A": "ECoN-weighted bond length",
    "quadratic_elongation": "Quadratic elongation",
    "bond_angle_variance_deg2": "Bond-angle variance",
    "bond_valence_sum_vu": "Bond-valence sum",
    "off_centring_A": "Off-centring",
    "corner_shared_per_polyhedron": "Corner links",
    "edge_shared_per_polyhedron": "Edge links",
    "face_shared_per_polyhedron": "Face links",
    "corner_shared": "Corner links",
    "edge_shared": "Edge links",
    "face_shared": "Face links",
    "mean_corner_bridging_angle_deg": "Bridging angle",
    "structure": "Structure",
    "formula": "Formula",
    "space_group": "Space group",
    "oxidation_state": "Oxidation state",
    "x": "x",
    "y": "y",
    "z": "z",
    "site": "Site",
    "multiplicity_in_cell": "Multiplicity",
    "forms_polyhedron": "Forms polyhedron",
    "reason": "Reason",
    "symmetry_consistent": "Symmetry consistent",
    "shannon_cn": "Shannon CN",
    "shannon_consistent": "Shannon consistent",
    "center_radius_A": "Centre radius",
    "ligand_radius_A": "Ligand radius",
    "expected_bond_length_A": "Expected bond length",
    "radius_ratio": "Radius ratio r+/r−",
    "pauling_cn": "Pauling CN",
    "ionic_radius_A": "Ionic radius",
    "ionic_radius_cn": "Ionic radius CN",
    "polyhedron_types": "Polyhedron types",
    "check": "Check",
    "detail": "Detail",
    "result": "Result",
}
UNIT_SUFFIXES = (
    ("_A3", "Å³"),
    ("_deg2", "deg²"),
    ("_deg", "°"),
    ("_vu", "v.u."),
    ("_A", "Å"),
)
_TYPE_METRICS = [col for _, col in METRIC_COLUMNS if col not in ("CN", "shape")]
# The columns of each typed table, in display order (so an empty table keeps its headings).
COLUMNS = {
    "types": [
        "type_id",
        "center",
        "ligand_formula",
        "CN",
        "shape",
        "kind",
        "element",
        "wyckoff",
        "site_symmetry",
        "n_in_cell",
        *_TYPE_METRICS,
        "corner_shared_per_polyhedron",
        "edge_shared_per_polyhedron",
        "face_shared_per_polyhedron",
        "mean_corner_bridging_angle_deg",
        "structure",
        "formula",
        "space_group",
    ],
    "polyhedra": [
        "center",
        "type_id",
        "wyckoff",
        "x",
        "y",
        "z",
        "CN",
        "shape",
        *_TYPE_METRICS,
        "corner_shared",
        "edge_shared",
        "face_shared",
        "mean_corner_bridging_angle_deg",
    ],
    "sites": [
        "site",
        "element",
        "oxidation_state",
        "wyckoff",
        "site_symmetry",
        "multiplicity_in_cell",
        "x",
        "y",
        "z",
        "CN",
        "ligand_formula",
        "shape",
        "forms_polyhedron",
        "reason",
        "symmetry_consistent",
        "shannon_cn",
        "shannon_consistent",
        "center_radius_A",
        "ligand_radius_A",
        "expected_bond_length_A",
        "radius_ratio",
        "pauling_cn",
        "ionic_radius_A",
        "ionic_radius_cn",
        "polyhedron_types",
    ],
    "checks": list(CHECK_COLUMNS),
}
CAPTIONS = {
    "types": (
        "One row per symmetry-distinct polyhedron of the cell. Lengths are in angstrom, "
        "volumes in cubic angstrom, angles in degrees."
    ),
    "polyhedra": (
        "Every polyhedron in the cell, including symmetry-equivalent copies. Lengths are in "
        "angstrom, volumes in cubic angstrom, angles in degrees; x, y, z are fractional."
    ),
    "sites": (
        "Coordination environment of each Wyckoff site and whether it forms a polyhedron. "
        "Radii and distances are in angstrom."
    ),
    "checks": (
        "Crystal-chemical checks (Pauling rules, bond valence, site symmetry). "
        "Result is pass, warn, fail or info; they are diagnostics, not validation."
    ),
}


def _unit_of(column: str) -> str | None:
    return next((unit for suffix, unit in UNIT_SUFFIXES if column.endswith(suffix)), None)


def _number(text: object) -> object:
    """A CSV cell as a number when it is one, as None when empty, else as it was."""
    if text == "":
        return None
    if isinstance(text, bool):
        return "yes" if text else "no"
    if isinstance(text, str):
        if "_" in text:
            return text
        try:
            return int(text)
        except ValueError:
            try:
                value = float(text)
            except ValueError:
                return text
            # nan and inf are not JSON numbers: keep the cell as written.
            return value if math.isfinite(value) else text
    if isinstance(text, float) and not math.isfinite(text):
        return str(text)
    return text


def typed_table(report: PolyhedraReport, table: str = "types", caption: str | None = None) -> dict:
    """One table of the report as a ``kind: "table"`` artifact with labels and units.

    ``table`` is "types" (the symmetry-distinct polyhedra), "polyhedra" (all of them), "sites"
    (the Wyckoff sites) or "checks" (the crystal-chemical checks). A report with no rows gives
    a table with its headings and no rows.
    """
    if table not in COLUMNS:
        raise ValueError(f"table must be one of {', '.join(COLUMNS)}")
    columns = COLUMNS[table]
    data = rows(report, table)
    return {
        "kind": "table",
        "columns": columns,
        "rows": [[_number(row.get(c, "")) for c in columns] for row in data],
        "labels": {c: LABELS[c] for c in columns if c in LABELS},
        "units": {c: u for c in columns if (u := _unit_of(c))},
        "caption": caption or CAPTIONS[table],
    }


def structure_summary(report: PolyhedraReport) -> dict:
    """A flat ``{label: value}`` object that the interface shows as a key/value list."""
    st, cfg = report.structure, report.settings
    lat, src = st.lattice, st.source
    out: dict[str, object] = {
        "Formula": st.reduced_formula,
        "Space group": f"{st.space_group.symbol} (#{st.space_group.number})",
        "Crystal system": st.space_group.crystal_system,
        "a (Å)": round(lat.a, 4),
        "b (Å)": round(lat.b, 4),
        "c (Å)": round(lat.c, 4),
        "α (°)": round(lat.alpha, 3),
        "β (°)": round(lat.beta, 3),
        "γ (°)": round(lat.gamma, 3),
    }
    if math.isfinite(lat.volume):
        out["Volume (Å³)"] = round(lat.volume, 3)
    out["Sites in cell"] = len(st.sites)
    out["Polyhedra in cell"] = len(report.polyhedra)
    out["Distinct polyhedra"] = len(report.polyhedron_types)
    out["Coordination method"] = cfg.method
    if cfg.method == "shannon" and cfg.radius_tolerance is not None:
        out["Radius tolerance"] = cfg.radius_tolerance
    if cfg.max_bond is not None:
        out["Max bond distance (Å)"] = cfg.max_bond
    out["Source"] = f"{src.kind}: {src.id}" if src.id else src.kind
    return out
