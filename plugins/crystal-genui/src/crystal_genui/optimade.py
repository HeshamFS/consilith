"""DFT-relaxed structures from open databases through OPTIMADE.

OPTIMADE (https://www.optimade.org, Andersen et al., Sci. Data 8, 217 (2021); Evans et al.,
Digital Discovery 3, 1509 (2024)) is the common REST API that most open materials databases
implement, so one client covers all of them. The registry below lists providers that serve
DFT-relaxed structures; any other OPTIMADE base URL can be passed directly.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

import numpy as np
from pymatgen.core import Composition, Lattice, Structure

USER_AGENT = "crystal-genui (OPTIMADE client)"
TIMEOUT = 30


@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    base_url: str  # without the trailing /v1
    level_of_theory: str
    notes: str = ""


# Open databases of computed (DFT-relaxed) structures with public OPTIMADE endpoints.
# The live registry of all providers is https://providers.optimade.org.
PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in [
        Provider("mp", "Materials Project", "https://optimade.materialsproject.org", "PBE / PBE+U, r2SCAN"),
        Provider("oqmd", "Open Quantum Materials Database", "https://oqmd.org/optimade", "PBE / PBE+U"),
        Provider("alexandria", "Alexandria (PBE)", "https://alexandria.icams.rub.de/pbe", "PBE"),
        Provider("alexandria_pbesol", "Alexandria (PBEsol)", "https://alexandria.icams.rub.de/pbesol", "PBEsol"),
        Provider("alexandria_scan", "Alexandria (SCAN)", "https://alexandria.icams.rub.de/scan", "SCAN"),
        Provider("aflow", "AFLOW", "https://aflow.org/API/optimade", "PBE / PBE+U"),
        Provider("jarvis", "JARVIS-DFT (NIST)", "https://jarvis.nist.gov/optimade/jarvisdft", "OptB88vdW"),
        Provider("nomad", "NOMAD", "https://nomad-lab.eu/prod/v1/optimade", "various (uploaded calculations)"),
        Provider("mc3d", "Materials Cloud 3D", "https://aiida.materialscloud.org/mc3d/optimade", "PBEsol (SSSP)"),
        Provider("mc2d", "Materials Cloud 2D", "https://aiida.materialscloud.org/mc2d/optimade", "PBE, exfoliable 2D"),
        Provider("odbx", "open database of xtals", "https://optimade.odbx.science", "PBE"),
    ]
}
DEFAULT_PROVIDERS = ["mp", "oqmd", "alexandria", "aflow", "jarvis", "mc3d"]


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.api+json, application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _base(provider: str) -> str:
    if provider in PROVIDERS:
        return PROVIDERS[provider].base_url.rstrip("/")
    if provider.startswith("http"):
        return provider.rstrip("/").removesuffix("/v1")
    raise ValueError(f"Unknown OPTIMADE provider {provider!r}; use one of {', '.join(PROVIDERS)} or a base URL.")


def optimade_formula(formula: str) -> str:
    """OPTIMADE chemical_formula_reduced: elements in alphabetical order, reduced integer counts."""
    comp = Composition(formula).reduced_composition
    return "".join(f"{el.symbol}{'' if n == 1 else int(n)}" for el, n in sorted(comp.items(), key=lambda kv: kv[0].symbol))


def build_filter(formula: str | None = None, elements: list[str] | None = None, exact_elements: bool = True) -> str:
    clauses = []
    if formula:
        clauses.append(f'chemical_formula_reduced="{optimade_formula(formula)}"')
    if elements:
        clauses.append("elements HAS ALL " + ",".join(f'"{e}"' for e in elements))
        if exact_elements:
            clauses.append(f"nelements={len(elements)}")
    if not clauses:
        raise ValueError("Give a formula or a list of elements.")
    return " AND ".join(clauses)


def to_structure(entry: dict) -> tuple[Structure, list[str]]:
    """Convert an OPTIMADE structures entry to a pymatgen Structure. Returns (structure, warnings)."""
    a = entry["attributes"]
    warnings: list[str] = []
    lattice = Lattice(np.array(a["lattice_vectors"], dtype=float))
    species_defs = {s["name"]: s for s in a.get("species") or []}
    site_species = []
    for name in a["species_at_sites"]:
        sdef = species_defs.get(name)
        if sdef is None:
            site_species.append(name)
            continue
        symbols = [s for s in sdef["chemical_symbols"] if s not in ("vacancy", "X")]
        conc = sdef.get("concentration") or [1.0] * len(sdef["chemical_symbols"])
        if len(symbols) == 1 and abs(sum(conc) - 1) < 1e-6:
            site_species.append(symbols[0])
        else:
            warnings.append(f"site species {name} is disordered ({sdef['chemical_symbols']}, {conc})")
            site_species.append({s: c for s, c in zip(sdef["chemical_symbols"], conc) if s not in ("vacancy", "X")})
    dims = a.get("dimension_types")
    if dims and sum(dims) < 3:
        warnings.append(f"structure is periodic in {sum(dims)} dimension(s) only (dimension_types={dims})")
    structure = Structure(lattice, site_species, a["cartesian_site_positions"], coords_are_cartesian=True)
    return structure, warnings


def _summary_row(provider: str, entry: dict) -> dict:
    a = entry.get("attributes", {})
    row = {
        "structure": f"optimade:{provider}:{entry['id']}",
        "database": PROVIDERS[provider].name if provider in PROVIDERS else provider,
        "id": entry["id"],
        "formula": a.get("chemical_formula_reduced") or a.get("chemical_formula_descriptive"),
        "n_sites": a.get("nsites"),
    }
    # Provider-specific properties (prefixed _<provider>_) that matter for picking a structure.
    keys = ("energy", "hull", "stab", "gap", "spacegroup", "space_group", "functional", "delta_e")
    for k, v in a.items():
        if k.startswith("_") and any(s in k.lower() for s in keys) and isinstance(v, (int, float, str)):
            row[k] = v
    return row


def search(
    formula: str | None = None,
    elements: list[str] | None = None,
    providers: list[str] | None = None,
    limit_per_provider: int = 10,
) -> dict:
    """Query several OPTIMADE providers in parallel. Returns {"results": [...], "errors": {provider: msg}}."""
    flt = build_filter(formula, elements)
    providers = providers or DEFAULT_PROVIDERS
    query = urllib.parse.urlencode({"filter": flt, "page_limit": limit_per_provider})

    def one(p):
        data = _get(f"{_base(p)}/v1/structures?{query}")
        return [_summary_row(p, e) for e in data.get("data", [])][:limit_per_provider]

    results, errors = [], {}
    with cf.ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futs = {pool.submit(one, p): p for p in providers}
        for fut in cf.as_completed(futs):
            p = futs[fut]
            try:
                results.extend(fut.result())
            except Exception as exc:  # one provider failing must not hide the others
                errors[p] = f"{type(exc).__name__}: {exc}"
    return {"filter": flt, "results": results, "errors": errors}


def fetch(provider: str, entry_id: str) -> tuple[Structure, dict]:
    """Fetch one structure by provider and id. Returns (structure, metadata)."""
    url = f"{_base(provider)}/v1/structures/{urllib.parse.quote(entry_id, safe='')}"
    data = _get(url)["data"]
    if isinstance(data, list):
        if not data:
            raise ValueError(f"{provider} has no structure {entry_id!r}")
        data = data[0]
    structure, warnings = to_structure(data)
    meta = _summary_row(provider, data)
    meta["url"] = url
    meta["warnings"] = warnings
    if provider in PROVIDERS:
        meta["level_of_theory"] = PROVIDERS[provider].level_of_theory
    return structure, meta
