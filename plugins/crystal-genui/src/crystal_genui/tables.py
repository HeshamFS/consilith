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

    raise ValueError(f"table must be one of {', '.join(TABLES)}")


def to_csv(report: PolyhedraReport, table: str = "polyhedra") -> str:
    data = rows(report, table)
    buf = io.StringIO()
    if data:
        w = csv.DictWriter(buf, fieldnames=list(data[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(data)
    return buf.getvalue()
