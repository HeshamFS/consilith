"""Load crystal structures from Materials Project, CIF text/files, or bundled examples."""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pymatgen.core import Lattice, Structure

MP_ID_RE = re.compile(r"^(mp|mvc)-\d+$", re.IGNORECASE)


@dataclass
class LoadedStructure:
    structure: Structure
    source_kind: str  # "materials_project" | "optimade" | "example" | "cif"
    source_id: str | None
    note: str | None = None
    retrieved_at: str | None = None


def _example_srtio3() -> Structure:
    return Structure.from_spacegroup(
        "Pm-3m", Lattice.cubic(3.905), ["Sr", "Ti", "O"], [[0, 0, 0], [0.5, 0.5, 0.5], [0, 0.5, 0.5]]
    )


def _example_batio3() -> Structure:
    return Structure.from_spacegroup(
        "P4mm",
        Lattice.tetragonal(3.9998, 4.0180),
        ["Ba", "Ti", "O", "O"],
        [[0, 0, 0], [0.5, 0.5, 0.482], [0.5, 0.5, 0.016], [0.5, 0, 0.515]],
    )


def _example_catio3() -> Structure:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return Structure.from_spacegroup(
            "Pbnm",
            Lattice.orthorhombic(5.3796, 5.4423, 7.6401),
            ["Ca", "Ti", "O", "O"],
            [[0.9936, 0.0349, 0.25], [0, 0.5, 0], [0.0711, 0.4835, 0.25], [0.7109, 0.2884, 0.0371]],
        )


def _example_bi2se3() -> Structure:
    return Structure.from_spacegroup(
        "R-3m",
        Lattice.hexagonal(4.143, 28.636),
        ["Bi", "Se", "Se"],
        [[0, 0, 0.4008], [0, 0, 0], [0, 0, 0.2117]],
    )


# Approximate experimental structures, bundled so everything works offline and without an
# API key. They are NOT DFT-relaxed; use a Materials Project id for that.
EXAMPLES = {
    "SrTiO3": (_example_srtio3, "Cubic perovskite Pm-3m, a = 3.905 Å (approx. experimental)"),
    "BaTiO3": (_example_batio3, "Tetragonal ferroelectric perovskite P4mm (approx. experimental)"),
    "CaTiO3": (_example_catio3, "Orthorhombic perovskite with tilted TiO6 octahedra, Pbnm (approx. experimental)"),
    "Bi2Se3": (_example_bi2se3, "Layered topological insulator R-3m (approx. experimental)"),
}


def list_examples() -> dict[str, str]:
    return {name: desc for name, (_, desc) in EXAMPLES.items()}


def get_mp_api_key() -> str | None:
    return os.environ.get("MP_API_KEY") or os.environ.get("PMG_MAPI_KEY")


def _mprester():
    key = get_mp_api_key()
    if not key:
        raise RuntimeError(
            "No Materials Project API key found. Set the MP_API_KEY environment variable "
            "(get a key at https://next-gen.materialsproject.org/api)."
        )
    try:
        from mp_api.client import MPRester
    except ImportError as exc:
        raise RuntimeError("Materials Project access requires crystal-genui[materials-project]") from exc

    return MPRester(key, mute_progress_bars=True)


def fetch_mp_structure(material_id: str, conventional: bool = True) -> LoadedStructure:
    """Fetch the DFT-relaxed structure for a Materials Project id (e.g. ``mp-5229``)."""
    with _mprester() as mpr:
        structure = mpr.get_structure_by_material_id(material_id, conventional_unit_cell=conventional)
    return LoadedStructure(
        structure=structure,
        source_kind="materials_project",
        source_id=material_id,
        note="DFT-relaxed structure from Materials Project" + (" (conventional cell)" if conventional else ""),
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def search_mp(
    formula: str | None = None,
    chemsys: str | None = None,
    elements: list[str] | None = None,
    max_energy_above_hull: float | None = 0.1,
    limit: int = 20,
) -> list[dict]:
    """Search Materials Project and return a short summary per material, most stable first."""
    fields = [
        "material_id",
        "formula_pretty",
        "symmetry",
        "energy_above_hull",
        "formation_energy_per_atom",
        "band_gap",
        "nsites",
        "is_stable",
    ]
    kwargs: dict = {"fields": fields}
    if formula:
        kwargs["formula"] = formula
    if chemsys:
        kwargs["chemsys"] = chemsys
    if elements:
        kwargs["elements"] = elements
    if max_energy_above_hull is not None:
        kwargs["energy_above_hull"] = (0, max_energy_above_hull)
    with _mprester() as mpr:
        docs = mpr.materials.summary.search(**kwargs)
    rows = [
        {
            "material_id": str(d.material_id),
            "formula": d.formula_pretty,
            "space_group": d.symmetry.symbol if d.symmetry else None,
            "crystal_system": str(d.symmetry.crystal_system) if d.symmetry else None,
            "energy_above_hull_eV_per_atom": d.energy_above_hull,
            "formation_energy_eV_per_atom": d.formation_energy_per_atom,
            "band_gap_eV": d.band_gap,
            "n_sites": d.nsites,
            "is_stable": d.is_stable,
        }
        for d in docs
    ]
    rows.sort(key=lambda r: (r["energy_above_hull_eV_per_atom"] is None, r["energy_above_hull_eV_per_atom"]))
    return rows[:limit]


def fetch_mp_properties(material_id: str) -> dict:
    """Thermodynamic, electronic and (when available) elastic properties for one material."""
    with _mprester() as mpr:
        docs = mpr.materials.summary.search(
            material_ids=[material_id],
            fields=[
                "material_id",
                "formation_energy_per_atom",
                "energy_above_hull",
                "band_gap",
                "is_stable",
                "density",
                "volume",
                "nsites",
                "bulk_modulus",
                "shear_modulus",
            ],
        )
        elastic_tensor = None
        try:
            edocs = mpr.materials.elasticity.search(material_ids=[material_id])
            if edocs and getattr(edocs[0], "elastic_tensor", None) is not None:
                et = edocs[0].elastic_tensor
                elastic_tensor = getattr(et, "ieee_format", None) or getattr(et, "raw", None)
        except Exception:  # elasticity data is optional and absent for most materials
            elastic_tensor = None
    if not docs:
        raise ValueError(f"No Materials Project entry for {material_id}")
    d = docs[0]

    def _vrh(m):
        if m is None:
            return None
        if isinstance(m, dict):
            return m.get("vrh")
        return getattr(m, "vrh", None)

    return {
        "formation_energy_per_atom": d.formation_energy_per_atom,
        "energy_above_hull": d.energy_above_hull,
        "band_gap": d.band_gap,
        "is_stable": d.is_stable,
        "bulk_modulus_vrh": _vrh(d.bulk_modulus),
        "shear_modulus_vrh": _vrh(d.shear_modulus),
        "elastic_tensor_voigt": [list(map(float, row)) for row in elastic_tensor] if elastic_tensor else None,
    }


def fetch_optimade_structure(provider: str, entry_id: str) -> LoadedStructure:
    from . import optimade

    structure, meta = optimade.fetch(provider, entry_id)
    note = f"DFT-relaxed structure from {meta['database']} via OPTIMADE"
    if meta.get("level_of_theory"):
        note += f" ({meta['level_of_theory']})"
    if meta["warnings"]:
        note += "; " + "; ".join(meta["warnings"])
    return LoadedStructure(
        structure=structure,
        source_kind="optimade",
        source_id=f"optimade:{provider}:{entry_id}",
        note=note,
        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def load_structure(spec: str, conventional: bool = True) -> LoadedStructure:
    """Resolve a structure spec.

    * ``mp-5229``                      Materials Project (native API, needs MP_API_KEY)
    * ``optimade:<provider>:<id>``     any OPTIMADE database, e.g. ``optimade:oqmd:4061139``,
      ``optimade:alexandria:agm002153184``, ``optimade:jarvis:JVASP-1002`` (no key needed)
    * an example name, CIF text, or a path to a CIF/POSCAR file
    """
    spec = spec.strip()
    if spec.lower().startswith("optimade:"):
        _, provider, entry_id = spec.split(":", 2)
        return fetch_optimade_structure(provider, entry_id)
    if MP_ID_RE.match(spec):
        return fetch_mp_structure(spec.lower(), conventional=conventional)
    for name, (builder, desc) in EXAMPLES.items():
        if spec.lower() == name.lower():
            return LoadedStructure(builder(), "example", name, desc)
    if "_cell_length_a" in spec or "data_" in spec[:200]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = Structure.from_str(spec, fmt="cif")
        return LoadedStructure(structure, "cif", None, "Parsed from CIF text")
    path = Path(spec).expanduser()
    if path.suffix.lower() in {".cif", ".vasp", ".poscar", ".json"} or path.name.upper() in {"POSCAR", "CONTCAR"}:
        if path.is_file():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                structure = Structure.from_file(path)
            return LoadedStructure(structure, "cif", path.name, f"Read from {path.name}")
    raise ValueError(
        f"Could not interpret {spec[:60]!r}. Pass a Materials Project id (e.g. mp-5229), "
        f"an example name ({', '.join(EXAMPLES)}), CIF text, or a path to a CIF/POSCAR file."
    )
