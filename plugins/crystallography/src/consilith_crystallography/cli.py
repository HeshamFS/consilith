"""Command line: analyze a structure, print VESTA-style text, or write a standalone HTML viewer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import analyze_spec, material_descriptor
from .tables import TABLES, to_csv


def _polyhedron_block(p, n_instances: int | None = None) -> list[str]:
    m = p.metrics
    head = f"== {p.type_id}: {p.center_label} at {p.center_wyckoff} ({', '.join(f'{x:.4f}' for x in p.center_frac)})"
    out = [
        head + (f", {n_instances} in cell" if n_instances else ""),
        f"   CN {m.coordination_number}, {m.shape}",
    ]
    out += [f"l({p.center_label}-{l.label}) = {l.distance:.5f} Å" for l in p.ligands]
    out.append("-" * 36)
    out.append(f"Average bond length = {m.average_bond_length:.4f} Å")
    if m.polyhedral_volume is not None:
        out.append(f"Polyhedral volume = {m.polyhedral_volume:.4f} Å^3")
    out.append(f"Distortion index (bond length) = {m.distortion_index:.5f}")
    if m.quadratic_elongation is not None:
        out.append(f"Quadratic elongation = {m.quadratic_elongation:.4f}")
    if m.bond_angle_variance is not None:
        out.append(f"Bond angle variance = {m.bond_angle_variance:.4f} deg^2")
    out.append(f"Effective coordination number = {m.effective_coordination_number:.4f}")
    if m.bond_valence_sum is not None:
        out.append(f"Bond valence sum = {m.bond_valence_sum:.3f} v.u.")
    out.append(f"Off-centring = {m.center_offset:.4f} Å")
    out.append("")
    return out


def vesta_text(report) -> str:
    """The workflow in text: site environments → which sites form polyhedra → analyses → rules."""
    st = report.structure
    cc = report.crystal_chemistry
    out = [
        f"{st.reduced_formula}  {st.space_group.symbol} (#{st.space_group.number}), {st.space_group.crystal_system}",
        f"Coordination by {report.settings.method}"
        + (
            f" (Shannon radii, tolerance {report.settings.radius_tolerance})"
            if report.settings.method == "shannon"
            else ""
        ),
        *[f"Note: {n}" for n in report.settings.notes],
        "",
        "1. Coordination environment of each Wyckoff site",
        f"{'site':<7}{'Wyckoff':<9}{'site sym':<10}{'ox':>4}{'CN':>4}  {'ligands':<10}{'shape':<36}polyhedron?",
    ]
    for e in report.site_environments:
        ox = "" if e.oxidation_state is None else f"{e.oxidation_state:+g}"
        verdict = "yes" if e.forms_polyhedron else f"no: {e.reason}"
        out.append(
            f"{e.site_label:<7}{e.wyckoff:<9}{e.site_symmetry:<10}{ox:>4}{e.coordination_number:>4}  {e.ligand_formula:<10}{e.shape[:35]:<36}{verdict}"
        )
    out += ["", "2. Analyses (one block per symmetry-distinct polyhedron)", ""]
    first = {}
    for p in report.polyhedra:
        first.setdefault(p.type_id, p)
    counts = {t.type_id: t.n_instances for t in report.polyhedron_types}
    for tid, p in first.items():
        out += _polyhedron_block(p, counts.get(tid))
    out += ["3. Crystal-chemical checks"]
    for r in cc.pauling_rule_1_radius_ratio:
        out.append(
            f"Pauling 1  {r['site']}: r+/r- = {r['radius_ratio']:.3f} → CN {r['predicted_cn']}, observed {r['observed_cn']} {'✓' if r['agrees'] else '✗'}"
        )
    for r in cc.pauling_rule_2_electrostatic_valence:
        out.append(
            f"Pauling 2  {r['site']}: Σ z/CN = {r['sum_of_electrostatic_bond_strengths']:.3f} vs {r['anion_charge']:g} {'✓' if r['satisfied'] else '✗'}"
        )
    for pair, c in cc.pauling_rule_3_polyhedron_sharing.items():
        out.append(
            f"Pauling 3  {pair}: "
            + ", ".join(f"{v} {k}" for k, v in c.items() if v)
            + " links in cell"
        )
    for r in cc.pauling_rule_4_high_valence_sharing:
        out.append(
            f"Pauling 4  {r['pair']} share an {r['sharing']} at {r['cation_distance']:.3f} Å (destabilising)"
        )
    out.append(
        "Pauling 5  distinct environments per element: "
        + ", ".join(f"{k} {v}" for k, v in cc.pauling_rule_5_parsimony.items())
    )
    if cc.global_instability_index is not None:
        out.append(
            f"Bond valence: GII = {cc.global_instability_index:.3f} v.u. ("
            + ", ".join(
                f"{r['site']} {r['bvs']:.2f}/{r['expected']:g}" for r in cc.bond_valence_sums
            )
            + ")"
        )
    if cc.bond_count_balance:
        b = cc.bond_count_balance
        out.append(
            f"Bond count: cations {b['cation_side']} = anions {b['anion_side']} {'✓' if b['balanced'] else '✗'}"
        )
    out.append(
        f"Site symmetry respected by every polyhedron: {'✓' if cc.site_symmetry_consistent else '✗'}"
    )
    return "\n".join(out)


def standalone_html(report) -> str:
    """The viewer with the report and its CSV tables embedded, for opening in any browser."""
    from .viewer import viewer_html

    data = report.model_dump_json(by_alias=True).replace("</", "<\\/")
    tables = json.dumps({t: to_csv(report, t) for t in TABLES}).replace("</", "<\\/")
    inject = f"<script>window.__CRYSTAL_GENUI_DATA__ = {data};\nwindow.__CRYSTAL_GENUI_CSV__ = {tables};</script>\n"
    return viewer_html().replace('<script>\n"use strict";', inject + '<script>\n"use strict";', 1)


def main() -> None:
    ap = argparse.ArgumentParser(prog="consilith-crystallography")
    ap.add_argument(
        "structure", help="Materials Project id, example name (SrTiO3, ...), or CIF/POSCAR path"
    )
    ap.add_argument("--centers", nargs="*")
    ap.add_argument("--ligands", nargs="*")
    ap.add_argument("--method", choices=["shannon", "crystalnn", "cutoff"], default="shannon")
    ap.add_argument("--tolerance", type=float, default=0.15, help="Shannon radius-sum tolerance")
    ap.add_argument(
        "--element-shells",
        action="store_true",
        help="also build nearest-shell polyhedra of each element",
    )
    ap.add_argument("--max-bond", type=float)
    ap.add_argument("--min-bond", type=float, default=0.0)
    ap.add_argument(
        "--format", choices=["text", "json", "html", "descriptor", "csv"], default="text"
    )
    ap.add_argument(
        "--table",
        choices=[*TABLES, "all"],
        default="polyhedra",
        help="CSV table; 'all' writes <output>_<table>.csv for each (needs -o)",
    )
    ap.add_argument("-o", "--output", type=Path)
    a = ap.parse_args()
    if a.format == "csv" and a.table == "all":
        if not a.output:
            ap.error("--table all needs -o PREFIX")

    kwargs = dict(
        centers=a.centers,
        ligands=a.ligands,
        method=a.method,
        max_bond=a.max_bond,
        min_bond=a.min_bond,
        radius_tolerance=a.tolerance,
        include_element_shells=a.element_shells,
    )
    if a.format == "csv" and a.table == "all":
        report = analyze_spec(a.structure, **kwargs)
        for t in TABLES:
            path = a.output.with_name(f"{a.output.stem}_{t}.csv")
            path.write_text(to_csv(report, t), encoding="utf-8")
            print(f"wrote {path}")
        return
    if a.format == "descriptor":
        text = material_descriptor(a.structure, **kwargs).model_dump_json(by_alias=True, indent=2)
    else:
        report = analyze_spec(a.structure, **kwargs)
        text = {
            "text": lambda: vesta_text(report),
            "json": lambda: report.model_dump_json(by_alias=True, indent=2),
            "html": lambda: standalone_html(report),
            "csv": lambda: to_csv(report, a.table),
        }[a.format]()
    if a.output:
        a.output.write_text(text, encoding="utf-8")
        print(f"wrote {a.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
