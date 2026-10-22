"""High-level API: turn a structure spec into schema documents."""

from __future__ import annotations

import numpy as np

from . import polyhedra as poly
from .rules import evaluate
from .schema import (
    AnalysisSettings,
    Connection,
    CrystalChemistry,
    ElasticData,
    LatticeRecord,
    LigandRecord,
    MaterialDescriptor,
    Polyhedron,
    PolyhedraReport,
    PolyhedronMetrics,
    PolyhedronTypeRecord,
    ShannonEnvironment,
    Site,
    SiteEnvironmentRecord,
    Source,
    SpaceGroup,
    StructureRecord,
)
from .structures import LoadedStructure, fetch_mp_properties, load_structure

AVOGADRO = 6.02214076e23


def _r(x, n=6):
    return None if x is None else round(float(x), n)


def _round_dict(d: dict) -> dict:
    return {k: (_r(v) if isinstance(v, float) else v) for k, v in d.items()}


def structure_record(loaded: LoadedStructure, sym: poly.SiteSymmetryInfo | None = None) -> StructureRecord:
    s = loaded.structure
    sym = sym or poly.site_symmetry(s)
    lat = s.lattice
    labels = poly.site_labels(s)
    return StructureRecord(
        source=Source(kind=loaded.source_kind, id=loaded.source_id, note=loaded.note, retrieved_at=loaded.retrieved_at),
        formula=s.composition.formula,
        reduced_formula=s.composition.reduced_formula,
        n_sites=len(s),
        density=_r(s.density),
        lattice=LatticeRecord(
            a=_r(lat.a), b=_r(lat.b), c=_r(lat.c),
            alpha=_r(lat.alpha), beta=_r(lat.beta), gamma=_r(lat.gamma),
            volume=_r(lat.volume),
            matrix=[[_r(x) for x in row] for row in lat.matrix],
        ),
        space_group=SpaceGroup(symbol=sym.space_group, number=sym.number, crystal_system=sym.crystal_system),
        sites=[
            Site(
                index=i,
                label=labels[i],
                element=poly._element(site),
                occupancy=float(sum(site.species.values())),
                frac=[_r(x) for x in site.frac_coords],
                cart=[_r(x) for x in site.coords],
                wyckoff=sym.wyckoff[i],
                site_symmetry=sym.site_symmetry[i],
                orbit=sym.orbit[i],
            )
            for i, site in enumerate(s)
        ],
    )


def analyze_structure(
    loaded: LoadedStructure,
    centers: list[str] | None = None,
    ligands: list[str] | None = None,
    method: str = "shannon",
    max_bond: float | None = None,
    min_bond: float = 0.0,
    radius_tolerance: float = 0.15,
    include_element_shells: bool = False,
) -> PolyhedraReport:
    s = loaded.structure
    results, types, envs, sym, settings = poly.analyze(
        s, centers, ligands, method, max_bond, min_bond, radius_tolerance, include_element_shells
    )
    labels = poly.site_labels(s)
    polys = []
    for p in results:
        polys.append(
            Polyhedron(
                type_id=p.type_id,
                center_index=p.center_index,
                center_label=labels[p.center_index],
                center_element=p.center_element,
                center_wyckoff=sym.wyckoff[p.center_index],
                center_frac=[_r(x) for x in s[p.center_index].frac_coords],
                center_cart=[_r(x) for x in p.center_cart],
                ligands=[
                    LigandRecord(
                        index=l.index, label=labels[l.index], element=l.element, image=list(l.image),
                        cart=[_r(x) for x in l.cart], distance=_r(l.distance),
                    )
                    for l in p.ligands
                ],
                metrics=PolyhedronMetrics(**_round_dict(p.metrics)),
                faces=p.faces,
                connections=[
                    Connection(
                        neighbor_label=labels[c["neighbor_index"]],
                        **{**c, "center_distance": _r(c["center_distance"]), "bridging_angles": [_r(a, 3) for a in c["bridging_angles"]]},
                    )
                    for c in p.connections
                ],
            )
        )

    type_records = []
    for t in types:
        inst = [polys[i] for i in t.instances]
        conn: dict[str, int] = {}
        bridging = []
        for p in inst:
            for c in p.connections:
                if c.neighbor_type_id == t.type_id:
                    conn[c.sharing] = conn.get(c.sharing, 0) + 1
                    if c.sharing == "corner":
                        bridging.extend(c.bridging_angles)
        type_records.append(
            PolyhedronTypeRecord(
                type_id=t.type_id,
                kind=t.kind,
                center_label=labels[t.representative_index],
                center_element=t.center_element,
                wyckoff=t.wyckoff,
                site_symmetry=t.site_symmetry,
                ligand_formula=t.ligand_formula,
                n_instances=len(inst),
                metrics=inst[0].metrics,
                connectivity={k: _r(v / len(inst), 3) for k, v in conn.items()},
                mean_bridging_angle=_r(np.mean(bridging), 3) if bridging else None,
            )
        )

    env_records = [
        SiteEnvironmentRecord(
            site_label=labels[e.representative_index],
            element=e.element,
            wyckoff=e.wyckoff,
            site_symmetry=e.site_symmetry,
            multiplicity_in_cell=len(e.orbit),
            representative_frac=[_r(x) for x in s[e.representative_index].frac_coords],
            oxidation_state=e.oxidation_state,
            coordination_number=e.coordination_number,
            ligand_formula=e.ligand_formula,
            shape=e.shape,
            forms_polyhedron=e.forms_polyhedron,
            reason=e.reason,
            symmetry_consistent=e.symmetry_consistent,
            shannon=ShannonEnvironment(**_round_dict(e.shannon)) if e.shannon else None,
            **_ionic_radius(e),
            type_ids=e.type_ids,
        )
        for e in envs
    ]
    checks = evaluate(results, types, envs, settings["oxidation_states"])
    return PolyhedraReport(
        structure=structure_record(loaded, sym),
        settings=AnalysisSettings(**settings),
        site_environments=env_records,
        polyhedron_types=type_records,
        polyhedra=polys,
        crystal_chemistry=CrystalChemistry(**checks),
    )


def _ionic_radius(env) -> dict:
    """Shannon radius of the site's ion at its observed CN (the nearest tabulated CN, lower on a tie)."""
    radii = poly.shannon_radii(env.element, env.oxidation_state)
    if not radii or env.coordination_number < 1:
        return {"ionic_radius": None, "ionic_radius_cn": None}
    cn = min(radii, key=lambda n: abs(n - env.coordination_number))
    return {"ionic_radius": radii[cn], "ionic_radius_cn": cn}


def analyze_spec(spec: str, **kwargs) -> PolyhedraReport:
    return analyze_structure(load_structure(spec), **kwargs)


def material_descriptor(spec: str, **analysis_kwargs) -> MaterialDescriptor:
    loaded = load_structure(spec)
    report = analyze_structure(loaded, **analysis_kwargs)
    s = loaded.structure
    comp = s.composition
    n_fu = comp.get_reduced_composition_and_factor()[1]
    molar_volume = s.volume * 1e-30 / n_fu * AVOGADRO
    props: dict = {}
    if loaded.source_kind == "materials_project":
        props = fetch_mp_properties(loaded.source_id)
    elastic = None
    if any(props.get(k) is not None for k in ("bulk_modulus_vrh", "shear_modulus_vrh", "elastic_tensor_voigt")):
        elastic = ElasticData(
            bulk_modulus_vrh=props.get("bulk_modulus_vrh"),
            shear_modulus_vrh=props.get("shear_modulus_vrh"),
            elastic_tensor_voigt=props.get("elastic_tensor_voigt"),
        )
    rec = report.structure
    return MaterialDescriptor(
        source=rec.source,
        formula=comp.reduced_formula,
        composition={el.symbol: _r(comp.get_atomic_fraction(el)) for el in comp.elements},
        atoms_per_formula_unit=int(round(comp.reduced_composition.num_atoms)),
        space_group=rec.space_group,
        lattice=rec.lattice,
        volume_per_atom=_r(s.volume / len(s)),
        molar_volume=float(f"{molar_volume:.6e}"),
        density=rec.density,
        formation_energy_per_atom=props.get("formation_energy_per_atom"),
        energy_above_hull=props.get("energy_above_hull"),
        band_gap=props.get("band_gap"),
        is_stable=props.get("is_stable"),
        elastic=elastic,
        global_instability_index=report.crystal_chemistry.global_instability_index,
        polyhedron_types=[t for t in report.polyhedron_types if t.kind == "coordination"],
    )
