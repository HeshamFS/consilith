"""Versioned JSON data contract shared with other MCP servers.

Three documents, each tagged with a ``schema`` string so a consumer can check what it got:

* ``crystallography/structure@1``           – a crystal structure (lattice + sites)
* ``crystallography/polyhedra@1``           – a structure plus its coordination polyhedra
* ``crystallography/material-descriptor@1`` – a compact, unit-explicit record meant as input to
  mesoscale models (e.g. phase-field): composition, molar volume, energetics, elastic constants
  and a polyhedral-distortion summary.

Lengths are Å, volumes Å^3 (molar volume m^3/mol), angles degrees, energies eV/atom,
moduli GPa. The JSON Schema files in ``schemas/`` are generated from these models
(``python -m consilith_crystallography.schema``) and must not be edited by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

STRUCTURE_SCHEMA = "crystallography/structure@1"
POLYHEDRA_SCHEMA = "crystallography/polyhedra@1"
DESCRIPTOR_SCHEMA = "crystallography/material-descriptor@1"


class Source(BaseModel):
    kind: Literal["materials_project", "optimade", "example", "cif"]
    id: str | None = Field(
        None,
        description="Materials Project id, optimade:<provider>:<id>, example name, or file name",
    )
    note: str | None = None
    retrieved_at: str | None = Field(None, description="ISO-8601 UTC time of retrieval")


class LatticeRecord(BaseModel):
    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float
    volume: float = Field(description="Cell volume, Å^3")
    matrix: list[list[float]] = Field(description="Row vectors a, b, c in Cartesian Å")


class SpaceGroup(BaseModel):
    symbol: str
    number: int
    crystal_system: str


class Site(BaseModel):
    index: int
    label: str = Field(
        description="Element + running number per element, 1-based (VESTA style, e.g. Bi7)"
    )
    element: str
    occupancy: float = 1.0
    frac: list[float]
    cart: list[float]
    wyckoff: str = Field(
        description="Multiplicity + Wyckoff letter in the conventional cell, e.g. 4b"
    )
    site_symmetry: str = Field(description="Oriented site-symmetry symbol (spglib), e.g. m-3m, .m.")
    orbit: int = Field(description="Index of the first site symmetry-equivalent to this one")


class StructureRecord(BaseModel):
    schema_: Literal["crystallography/structure@1"] = Field(STRUCTURE_SCHEMA, alias="schema")
    source: Source
    formula: str
    reduced_formula: str
    n_sites: int
    density: float = Field(description="g/cm^3")
    lattice: LatticeRecord
    space_group: SpaceGroup
    sites: list[Site]

    model_config = {"populate_by_name": True}


class LigandRecord(BaseModel):
    index: int
    label: str
    element: str
    image: list[int] = Field(
        description="Lattice translation of the ligand relative to the home cell"
    )
    cart: list[float]
    distance: float


class Connection(BaseModel):
    neighbor_index: int
    neighbor_label: str
    neighbor_element: str
    neighbor_type_id: str
    image: list[int]
    center_distance: float
    shared_ligands: int
    sharing: Literal["corner", "edge", "face"]
    bridging_angles: list[float] = Field(
        description="centre–ligand–neighbour angles for each shared ligand, degrees"
    )


class PolyhedronMetrics(BaseModel):
    coordination_number: int
    shape: str = Field(description="Identified from vertex count and face polygons of the hull")
    face_sizes: list[int] = Field(description="Number of vertices of each (merged) hull face")
    average_bond_length: float = Field(description="VESTA 'Average bond length', Å")
    min_bond_length: float
    max_bond_length: float
    polyhedral_volume: float | None = Field(
        description="VESTA 'Polyhedral volume' (convex hull), Å^3"
    )
    distortion_index: float = Field(description="VESTA 'Distortion index (bond length)', Baur 1974")
    effective_coordination_number: float = Field(
        description="VESTA 'Effective coordination number', Hoppe 1979"
    )
    weighted_average_bond_length: float = Field(
        description="Hoppe-weighted mean bond length used for ECoN, Å"
    )
    quadratic_elongation: float | None = Field(
        description="Robinson 1971; tetrahedra, octahedra, cubes"
    )
    bond_angle_variance: float | None = Field(
        description="Robinson 1971, deg^2; tetrahedra, octahedra, cubes"
    )
    bond_valence_sum: float | None = Field(
        description="Brown bond-valence sum, Brese & O'Keeffe 1991 R0, b=0.37 Å"
    )
    center_offset: float = Field(description="Distance of the centre from the ligand centroid, Å")


class Polyhedron(BaseModel):
    type_id: str
    center_index: int
    center_label: str
    center_element: str
    center_wyckoff: str
    center_frac: list[float]
    center_cart: list[float]
    ligands: list[LigandRecord]
    metrics: PolyhedronMetrics
    faces: list[list[int]] = Field(description="Convex-hull triangles as indices into ligands")
    connections: list[Connection]


class ShannonEnvironment(BaseModel):
    shannon_cn: int = Field(description="Coordination number whose Shannon radius gave the bonds")
    shannon_consistent: bool = Field(
        description="True if exactly shannon_cn neighbours fall within the radius sum"
    )
    center_radius: float = Field(description="Shannon ionic radius of the centre at shannon_cn, Å")
    ligand_radius: float = Field(description="Mean Shannon ionic radius of the ligands, Å")
    expected_bond_length: float = Field(description="center_radius + ligand_radius, Å")
    radius_ratio: float = Field(description="r_cation / r_anion")
    pauling_cn: int | None = Field(None, description="CN predicted by Pauling's radius-ratio rule")


class SiteEnvironmentRecord(BaseModel):
    """Step 1–2 of the workflow for one symmetry-distinct site."""

    site_label: str
    element: str
    wyckoff: str
    site_symmetry: str
    multiplicity_in_cell: int
    representative_frac: list[float]
    oxidation_state: float | None
    coordination_number: int
    ligand_formula: str
    shape: str
    forms_polyhedron: bool
    reason: str = Field(description="Why the site can or cannot be a polyhedron centre")
    symmetry_consistent: bool | None = Field(
        description="Polyhedron invariant under the site-symmetry group"
    )
    shannon: ShannonEnvironment | None = None
    ionic_radius: float | None = Field(
        None,
        description="Shannon (1976) ionic radius of this ion in Å at its observed coordination (nearest tabulated CN)",
    )
    ionic_radius_cn: int | None = Field(
        None, description="Coordination number the ionic radius is tabulated for"
    )
    type_ids: list[str]


class PolyhedronTypeRecord(BaseModel):
    """One symmetry-distinct polyhedron; all its instances have identical metrics."""

    type_id: str
    kind: Literal["coordination", "element_shell"]
    center_label: str
    center_element: str
    wyckoff: str
    site_symmetry: str
    ligand_formula: str
    n_instances: int
    metrics: PolyhedronMetrics
    connectivity: dict[str, float] = Field(
        description="Average number of corner/edge/face links per polyhedron to polyhedra of the same type"
    )
    mean_bridging_angle: float | None = Field(
        description="Mean centre–ligand–centre angle over corner-sharing links within the family, degrees"
    )


class CrystalChemistry(BaseModel):
    pauling_rule_1_radius_ratio: list[dict]
    pauling_rule_2_electrostatic_valence: list[dict]
    pauling_rule_3_polyhedron_sharing: dict[str, dict[str, int]]
    pauling_rule_4_high_valence_sharing: list[dict]
    pauling_rule_5_parsimony: dict[str, int]
    bond_valence_sums: list[dict]
    global_instability_index: float | None = Field(description="sqrt(<(BVS-|V|)^2>), valence units")
    bond_count_balance: dict | None
    site_symmetry_consistent: bool


class AnalysisSettings(BaseModel):
    method: Literal["shannon", "crystalnn", "cutoff"]
    centers: list[str]
    ligands: list[str] | None = None
    max_bond: float | None = None
    min_bond: float = 0.0
    radius_tolerance: float | None = None
    include_element_shells: bool = False
    oxidation_states: dict[str, float] | None = None
    notes: list[str] = []


class PolyhedraReport(BaseModel):
    schema_: Literal["crystallography/polyhedra@1"] = Field(POLYHEDRA_SCHEMA, alias="schema")
    structure: StructureRecord
    settings: AnalysisSettings
    site_environments: list[SiteEnvironmentRecord]
    polyhedron_types: list[PolyhedronTypeRecord]
    polyhedra: list[Polyhedron]
    crystal_chemistry: CrystalChemistry

    model_config = {"populate_by_name": True}


class ElasticData(BaseModel):
    bulk_modulus_vrh: float | None = Field(None, description="GPa")
    shear_modulus_vrh: float | None = Field(None, description="GPa")
    elastic_tensor_voigt: list[list[float]] | None = Field(
        None, description="6x6 C_ij, GPa, IEEE convention"
    )


class MaterialDescriptor(BaseModel):
    schema_: Literal["crystallography/material-descriptor@1"] = Field(
        DESCRIPTOR_SCHEMA, alias="schema"
    )
    source: Source
    formula: str
    composition: dict[str, float] = Field(description="Atomic fractions")
    atoms_per_formula_unit: int = Field(description="Atoms in one reduced formula unit")
    space_group: SpaceGroup
    lattice: LatticeRecord
    volume_per_atom: float = Field(description="Å^3/atom")
    molar_volume: float = Field(description="m^3 per mole of formula units (reduced formula)")
    density: float = Field(description="g/cm^3")
    formation_energy_per_atom: float | None = Field(
        None, description="eV/atom (DFT, Materials Project)"
    )
    energy_above_hull: float | None = Field(None, description="eV/atom")
    band_gap: float | None = Field(None, description="eV")
    is_stable: bool | None = None
    elastic: ElasticData | None = None
    global_instability_index: float | None = Field(
        None, description="Bond-valence global instability index, v.u."
    )
    polyhedron_types: list[PolyhedronTypeRecord]

    model_config = {"populate_by_name": True}


SCHEMAS = {
    "structure.v1.schema.json": StructureRecord,
    "polyhedra.v1.schema.json": PolyhedraReport,
    "material-descriptor.v1.schema.json": MaterialDescriptor,
}


def json_schemas() -> dict[str, dict]:
    out = {}
    for name, model in SCHEMAS.items():
        s = model.model_json_schema(by_alias=True)
        s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        out[name] = s
    return out


def write_schemas(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, s in json_schemas().items():
        (directory / name).write_text(json.dumps(s, indent=2) + "\n")


if __name__ == "__main__":
    write_schemas(Path(__file__).resolve().parent / "schemas")
