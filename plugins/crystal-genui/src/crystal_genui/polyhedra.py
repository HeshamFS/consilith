"""VESTA-style coordination-polyhedron analysis.

Definitions (the same ones VESTA reports):

* Average bond length          l_av = (1/n) Σ l_i
* Polyhedral volume            volume of the convex hull of the n ligand positions
* Distortion index (Baur 1974) D = (1/n) Σ |l_i − l_av| / l_av
* Effective coordination number (Hoppe 1979, as implemented in VESTA)
      l_w  = Σ l_i exp[1 − (l_i/l_min)^6] / Σ exp[1 − (l_i/l_min)^6]
      ECoN = Σ exp[1 − (l_i/l_w)^6]
* Quadratic elongation (Robinson, Gibbs & Ribbe 1971)  <λ> = (1/n) Σ (l_i/l_0)^2,
  l_0 = centre-to-vertex distance of the regular polyhedron with the same volume
  (tetrahedra, octahedra and cubes only)
* Bond-angle variance (Robinson 1971)  σ² = Σ (θ_i − θ_0)² / (m − 1) over the m
  ligand–centre–ligand angles along polyhedron edges (θ_0 = 109.47°, 90°, 70.53°)

Beyond VESTA, each centre also gets a Brown bond-valence sum (Brese & O'Keeffe 1991
universal R_0, b = 0.37 Å), the off-centring of the cation from the ligand centroid,
and the corner/edge/face connectivity to neighbouring polyhedra with the bridging
centre–ligand–centre angles (octahedral tilts in perovskites).
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from pymatgen.analysis.bond_valence import BV_PARAMS
from pymatgen.core import Element, Species, Structure
from scipy.spatial import ConvexHull, QhullError

BV_B = 0.37  # Å, Brown & Altermatt softness parameter

# Tabulated R0 (Å) from Brese & O'Keeffe, Acta Cryst. B47, 192 (1991), for cations with a single
# common oxidation state. Other pairs fall back to the same paper's electronegativity-based estimate.
BV_R0_TABLE = {
    ("Li", "O"): 1.466, ("Na", "O"): 1.803, ("K", "O"): 2.132, ("Mg", "O"): 1.693,
    ("Ca", "O"): 1.967, ("Sr", "O"): 2.118, ("Ba", "O"): 2.285, ("Zn", "O"): 1.704,
    ("Al", "O"): 1.651, ("Si", "O"): 1.624, ("Sc", "O"): 1.849, ("Y", "O"): 2.019,
    ("La", "O"): 2.172, ("Ti", "O"): 1.815, ("Zr", "O"): 1.937, ("Nb", "O"): 1.911,
    ("Ta", "O"): 1.920, ("Bi", "O"): 2.094, ("Pb", "O"): 2.112,
    ("Pb", "Cl"): 2.53, ("Pb", "Br"): 2.68, ("Pb", "I"): 2.83,
}

# Regular-polyhedron reference shapes: CN -> (name, V(l0) = k * l0^3, ideal edge angle θ0 in degrees, n edges)
REGULAR = {
    4: ("tetrahedron", 8.0 / (9.0 * math.sqrt(3.0)), 109.4712206, 6),
    6: ("octahedron", 4.0 / 3.0, 90.0, 12),
    8: ("cube", 8.0 / (3.0 * math.sqrt(3.0)), 70.5287794, 12),
}

# Polyhedron identification from the convex hull: (vertices, sorted face sizes) -> name.
# Faces are hull facets merged when their normals differ by less than FACE_MERGE_DEG.
FACE_MERGE_DEG = 8.0
SHAPES = {
    (4, (3, 3, 3, 3)): "tetrahedron",
    (5, (3,) * 6): "trigonal bipyramid",
    (5, (3, 3, 3, 3, 4)): "square pyramid",
    (6, (3,) * 8): "octahedron",
    (6, (3, 3, 4, 4, 4)): "trigonal prism",
    (6, (3, 3, 3, 3, 3, 5)): "pentagonal pyramid",
    (7, (3,) * 10): "pentagonal bipyramid / capped octahedron",
    (7, (3, 3, 3, 3, 4, 4)): "capped trigonal prism",
    (7, (3, 3, 3, 3, 3, 3, 4, 4)): "capped trigonal prism",
    (8, (4,) * 6): "cube",
    (8, (3,) * 8 + (4, 4)): "square antiprism",
    (8, (3,) * 12): "triangular dodecahedron / hexagonal bipyramid",
    (8, (3,) * 10 + (4,)): "bicapped trigonal prism",
    (9, (3,) * 14): "tricapped trigonal prism",
    (9, (3,) * 12 + (4,)): "capped square antiprism",
    (10, (3,) * 16): "bicapped square antiprism",
    (12, (3,) * 8 + (4,) * 6): "cuboctahedron / anticuboctahedron",
    (12, (3,) * 20): "icosahedron",
    (12, (4,) * 6 + (6, 6)): "hexagonal prism",
}


@dataclass
class Ligand:
    index: int
    element: str
    image: tuple[int, int, int]
    cart: np.ndarray
    distance: float


@dataclass
class PolyhedronResult:
    center_index: int
    center_element: str
    center_cart: np.ndarray
    ligands: list[Ligand]
    metrics: dict
    faces: list[list[int]] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    type_id: str = ""


# ---------------------------------------------------------------- metrics


def average_bond_length(lengths: np.ndarray) -> float:
    return float(np.mean(lengths))


def distortion_index(lengths: np.ndarray) -> float:
    lav = np.mean(lengths)
    return float(np.mean(np.abs(lengths - lav)) / lav)


def effective_coordination_number(lengths: np.ndarray) -> tuple[float, float]:
    """Hoppe ECoN as VESTA computes it. Returns (ECoN, weighted mean bond length)."""
    lmin = float(np.min(lengths))
    w = np.exp(1.0 - (lengths / lmin) ** 6)
    lw = float(np.sum(lengths * w) / np.sum(w))
    econ = float(np.sum(np.exp(1.0 - (lengths / lw) ** 6)))
    return econ, lw


def hull(points: np.ndarray) -> ConvexHull | None:
    if len(points) < 4:
        return None
    try:
        return ConvexHull(points)
    except QhullError:  # coplanar ligands (e.g. square-planar) enclose no volume
        return None


def polyhedral_volume(points: np.ndarray) -> float | None:
    h = hull(points)
    return float(h.volume) if h is not None else None


def _edge_pairs(points: np.ndarray, h: ConvexHull) -> set[tuple[int, int]]:
    """Ligand pairs joined by a true polyhedron edge (coplanar facet diagonals removed)."""
    pairs: dict[tuple[int, int], list[int]] = {}
    for f, simplex in enumerate(h.simplices):
        for a in range(3):
            i, j = sorted((int(simplex[a]), int(simplex[(a + 1) % 3])))
            pairs.setdefault((i, j), []).append(f)
    edges = set()
    for pair, facets in pairs.items():
        if len(facets) == 2:
            n1, n2 = h.equations[facets[0], :3], h.equations[facets[1], :3]
            if np.dot(n1, n2) > math.cos(math.radians(FACE_MERGE_DEG)):  # diagonal of a merged face
                continue
        edges.add(pair)
    return edges


def angle_deg(a: np.ndarray, center: np.ndarray, b: np.ndarray) -> float:
    u, v = a - center, b - center
    c = np.dot(u, v) / (np.linalg.norm(u) * np.linalg.norm(v))
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def merged_faces(h: ConvexHull) -> list[list[int]]:
    """Hull facets merged into polygons (vertex index lists) when nearly coplanar."""
    n = len(h.simplices)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cos_tol = math.cos(math.radians(FACE_MERGE_DEG))
    for f in range(n):
        for g in h.neighbors[f]:
            if np.dot(h.equations[f, :3], h.equations[g, :3]) > cos_tol:
                parent[find(f)] = find(g)
    groups: dict[int, set[int]] = {}
    for f in range(n):
        groups.setdefault(find(f), set()).update(int(v) for v in h.simplices[f])
    return [sorted(v) for v in groups.values()]


def identify_shape(points: np.ndarray) -> tuple[str, list[int]]:
    """Name the polyhedron from its vertex count and face polygons. Returns (name, face sizes)."""
    h = hull(points)
    n = len(points)
    if h is None:
        return ("planar" if n >= 3 else "linear" if n == 2 else "single"), []
    sizes = sorted(len(f) for f in merged_faces(h))
    if len(h.vertices) < n:
        return f"{n}-ligand cluster ({n - len(h.vertices)} ligand(s) inside the hull)", sizes
    name = SHAPES.get((n, tuple(sizes)))
    if name is None:
        faces = ", ".join(f"{sizes.count(k)}×{k}-gon" for k in sorted(set(sizes)))
        name = f"{n}-vertex polyhedron ({faces})"
    return name, sizes


def quadratic_elongation_and_bav(center: np.ndarray, points: np.ndarray, shape: str | None = None) -> tuple[float | None, float | None]:
    """Robinson <λ> and σ² for polyhedra whose topology is a tetrahedron, octahedron or cube."""
    if shape is None:
        shape = identify_shape(points)[0]
    ref = {"tetrahedron": 4, "octahedron": 6, "cube": 8}.get(shape)
    if ref is None or len(points) != ref:
        return None, None
    _, k, theta0, n_edges = REGULAR[ref]
    h = hull(points)
    l0 = (h.volume / k) ** (1.0 / 3.0)
    lengths = np.linalg.norm(points - center, axis=1)
    qe = float(np.mean((lengths / l0) ** 2))
    edges = _edge_pairs(points, h)
    if len(edges) != n_edges:
        return qe, None
    angles = np.array([angle_deg(points[i], center, points[j]) for i, j in edges])
    bav = float(np.sum((angles - theta0) ** 2) / (len(angles) - 1))
    return qe, bav


def bv_r0(cation: str, anion: str) -> float | None:
    """Brese & O'Keeffe (1991) bond-valence parameter R0: tabulated if available, else their universal estimate."""
    if (cation, anion) in BV_R0_TABLE:
        return BV_R0_TABLE[(cation, anion)]
    try:
        pc, pa = BV_PARAMS[Element(cation)], BV_PARAMS[Element(anion)]
    except KeyError:
        return None
    rc, cc, ra, ca = pc["r"], pc["c"], pa["r"], pa["c"]
    return rc + ra - rc * ra * (math.sqrt(cc) - math.sqrt(ca)) ** 2 / (cc * rc + ca * ra)


def bond_valence_sum(center: str, ligands: list[Ligand], anion_center: bool = False) -> float | None:
    """Σ exp((R0 − d)/b). For an anion centre the pairs are (ligand cation, centre anion)."""
    total = 0.0
    for lig in ligands:
        r0 = bv_r0(lig.element, center) if anion_center else bv_r0(center, lig.element)
        if r0 is None:
            return None
        total += math.exp((r0 - lig.distance) / BV_B)
    return total


# ---------------------------------------------------------------- neighbour selection


def _element(site) -> str:
    return site.specie.symbol if hasattr(site.specie, "symbol") else str(site.specie)


def _oxidation_states(structure: Structure) -> dict[str, float]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            guesses = structure.composition.oxi_state_guesses(max_sites=-50)
    except Exception:
        return {}
    return dict(guesses[0]) if guesses else {}


def counter_ions(center: str, present: list[str], oxi: dict[str, float]) -> list[str]:
    """Ligands of a centre: anions around a cation, cations around an anion (e.g. OTi2Sr4 around O).

    Uses the guessed oxidation states; without them (intermetallics) the most electronegative
    element is the anion, and if the centre is that element every other element is a ligand.
    """
    if oxi and center in oxi:
        sign = np.sign(oxi[center])
        others = [el for el in present if el in oxi and np.sign(oxi[el]) == -sign and sign != 0]
        if others:
            return others
    anion = max(present, key=lambda e: (Element(e).X if not math.isnan(Element(e).X) else 0.0))
    if center != anion:
        return [anion]
    return [el for el in present if el != center] or [center]


ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
         "XI": 11, "XII": 12, "XIV": 14}


def shannon_radii(element: str, oxi: float | None, radius_type: str = "ionic") -> dict[int, float]:
    """Shannon (1976) radii of an ion for every tabulated coordination number, {CN: radius in Å}.

    Data: R. D. Shannon, Acta Cryst. A32, 751 (1976), as shipped with pymatgen (the same table
    served by the Imperial College database, abulafia.mt.ic.ac.uk/shannon). Where a spin state
    is listed separately, high spin is used.
    """
    if oxi is None:
        return {}
    try:
        table = Element(element).data.get("Shannon radii", {}).get(str(int(round(oxi))), {})
    except Exception:
        return {}
    out = {}
    for roman, spins in table.items():
        if roman.rstrip("SQPY") not in ROMAN:  # skip square-planar / pyramidal variants (IVSQ, IVPY)
            continue
        if roman != roman.rstrip("SQPY"):
            continue
        entry = spins.get("") or spins.get("High Spin") or next(iter(spins.values()))
        out[ROMAN[roman]] = float(entry[f"{radius_type}_radius"])
    return dict(sorted(out.items()))


def ligand_radius(element: str, oxi: float | None, radius_type: str = "ionic") -> float | None:
    """Radius of a ligand ion at CN 6 (Shannon's reference), or the closest tabulated CN."""
    radii = shannon_radii(element, oxi, radius_type)
    if not radii:
        return None
    return radii[min(radii, key=lambda cn: abs(cn - 6))]


def pauling_cn(radius_ratio: float) -> int:
    """Pauling's radius-ratio rule: smallest coordination the cation can fill without rattling."""
    for limit, cn in ((0.155, 2), (0.225, 3), (0.414, 4), (0.732, 6), (1.0, 8)):
        if radius_ratio < limit:
            return cn
    return 12


def shannon_shell(
    structure: Structure,
    center_index: int,
    ligand_elements: list[str],
    oxi: dict[str, float],
    tolerance: float,
    radius_type: str = "ionic",
) -> tuple[list[Ligand], dict | None]:
    """Self-consistent coordination from Shannon radii.

    For every coordination number n tabulated for the centre ion, a neighbour is bonded if
    d ≤ (r_centre(n) + r_ligand)(1 + tolerance). The environment is the n for which exactly
    n neighbours qualify (the largest such n if several do). If none is self-consistent, the n
    whose count is closest is used and ``consistent`` is False.
    """
    site = structure[center_index]
    el = _element(site)
    rc = shannon_radii(el, oxi.get(el), radius_type)
    rl = {e: ligand_radius(e, oxi.get(e), radius_type) for e in ligand_elements}
    rl = {e: r for e, r in rl.items() if r is not None}
    if not rc or not rl:
        raise ValueError(f"No Shannon radius for {el} (oxidation {oxi.get(el)}) or its ligands {ligand_elements}")
    rmax = (max(rc.values()) + max(rl.values())) * (1 + tolerance) + 0.01
    nns = sorted(
        (nn for nn in structure.get_neighbors(site, rmax) if _element(nn) in rl and nn.nn_distance > 0.5),
        key=lambda nn: nn.nn_distance,
    )
    trials = []
    for n, r in rc.items():
        chosen = [nn for nn in nns if nn.nn_distance <= (r + rl[_element(nn)]) * (1 + tolerance)]
        trials.append((n, r, chosen))
    consistent = [t for t in trials if len(t[2]) == t[0]]
    if consistent:
        n, r, chosen = consistent[-1]
    else:
        n, r, chosen = min(trials, key=lambda t: (abs(len(t[2]) - t[0]), -t[0]))
    ligs = [Ligand(int(nn.index), _element(nn), tuple(int(x) for x in nn.image), nn.coords.copy(), float(nn.nn_distance)) for nn in chosen]
    mean_rl = float(np.mean([rl[l.element] for l in ligs])) if ligs else None
    info = {
        "shannon_cn": n,
        "shannon_consistent": bool(consistent),
        "center_radius": r,
        "ligand_radius": mean_rl,
        "expected_bond_length": (r + mean_rl) if mean_rl else None,
        "radius_ratio": (r / mean_rl) if mean_rl and oxi.get(el, 1) > 0 else (mean_rl / r if mean_rl else None),
    }
    info["pauling_cn"] = pauling_cn(info["radius_ratio"]) if info["radius_ratio"] else None
    return sorted(ligs, key=lambda l: l.distance), info


def find_ligands(
    structure: Structure,
    center_index: int,
    ligand_elements: list[str],
    method: str,
    max_bond: float | None,
    min_bond: float,
    radius_tolerance: float,
    oxi: dict[str, float],
    cnn=None,
) -> list[Ligand]:
    site = structure[center_index]
    out: list[Ligand] = []
    if method == "crystalnn":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            nn_info = cnn.get_nn_info(structure, center_index)
        for info in nn_info:
            nsite = info["site"]
            el = _element(nsite)
            if el not in ligand_elements:
                continue
            d = float(np.linalg.norm(nsite.coords - site.coords))
            out.append(Ligand(int(info["site_index"]), el, tuple(int(x) for x in info["image"]), nsite.coords.copy(), d))
        return sorted(out, key=lambda l: l.distance)

    if method == "shannon":
        return shannon_shell(structure, center_index, ligand_elements, oxi, radius_tolerance)[0]
    if True:  # cutoff
        if max_bond is None:
            raise ValueError("method='cutoff' needs max_bond (Å).")
        limits = {el: max_bond for el in ligand_elements}
        rmax = max_bond

    for nn in structure.get_neighbors(site, rmax + 1e-6):
        el = _element(nn)
        lim = limits.get(el)
        if lim is None or not (min_bond <= nn.nn_distance <= lim):
            continue
        out.append(Ligand(int(nn.index), el, tuple(int(x) for x in nn.image), nn.coords.copy(), float(nn.nn_distance)))
    return sorted(out, key=lambda l: l.distance)


def element_shell(
    structure: Structure, center_index: int, element: str, gap: float = 1.10, max_spread: float = 1.6
) -> list[Ligand]:
    """Nearest coordination shell of one element around a site.

    The shell is closed at the first jump in distance by more than ``gap`` (10 %) once it holds
    at least four atoms, and never extends beyond ``max_spread`` × the nearest distance. This is
    how a crystallographer would pick, e.g., the Ti8 cube around Sr or the Ti6 octahedron around Ti
    in SrTiO3 by eye.
    """
    site = structure[center_index]
    for r in (6.0, 10.0, 14.0):
        nns = [nn for nn in structure.get_neighbors(site, r) if _element(nn) == element and nn.nn_distance > 0.5]
        if len(nns) >= 5:
            break
    nns.sort(key=lambda nn: nn.nn_distance)
    if len(nns) < 4:
        return []
    d = [nn.nn_distance for nn in nns]
    k = len(nns)
    for i in range(4, len(nns)):
        if d[i] / d[i - 1] > gap:
            k = i
            break
    if d[k - 1] > max_spread * d[0]:
        return []
    return [Ligand(int(nn.index), element, tuple(int(x) for x in nn.image), nn.coords.copy(), float(nn.nn_distance)) for nn in nns[:k]]


# ---------------------------------------------------------------- symmetry


@dataclass
class SiteSymmetryInfo:
    space_group: str
    number: int
    crystal_system: str
    orbit: list[int]  # index of the representative (first) site of each site's orbit
    wyckoff: list[str]  # e.g. "4b"
    site_symmetry: list[str]  # e.g. "-1", "m.mm"
    rotations: list = field(default_factory=list)  # space-group operations on fractional coordinates
    translations: list = field(default_factory=list)


def site_symmetry(structure: Structure, symprec: float = 0.01) -> SiteSymmetryInfo:
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sga = SpacegroupAnalyzer(structure, symprec=symprec)
        ds = sga.get_symmetry_dataset()
        n_conv = len(sga.get_conventional_standard_structure())
    equiv = [int(x) for x in ds.equivalent_atoms]
    scale = n_conv / len(structure)
    counts: dict[int, int] = {}
    for e in equiv:
        counts[e] = counts.get(e, 0) + 1
    wyckoff = [f"{int(round(counts[equiv[i]] * scale))}{ds.wyckoffs[i]}" for i in range(len(structure))]
    return SiteSymmetryInfo(
        space_group=sga.get_space_group_symbol(),
        number=sga.get_space_group_number(),
        crystal_system=sga.get_crystal_system(),
        orbit=equiv,
        wyckoff=wyckoff,
        site_symmetry=[str(x) for x in ds.site_symmetry_symbols],
        rotations=[np.array(r) for r in ds.rotations],
        translations=[np.array(t) for t in ds.translations],
    )


def symmetry_consistent(structure: Structure, sym: SiteSymmetryInfo, index: int, ligs: list[Ligand], tol: float = 1e-3) -> bool:
    """True if the polyhedron is invariant under every operation of the centre's site-symmetry group.

    A polyhedron on a special position must carry that position's point symmetry (e.g. the
    m-3m site of Ti in cubic SrTiO3 forces a regular octahedron).
    """
    if not ligs:
        return True
    lat = structure.lattice
    f0 = structure[index].frac_coords
    lf = np.array([lat.get_fractional_coords(l.cart) for l in ligs])
    for R, t in zip(sym.rotations, sym.translations):
        moved = R @ f0 + t
        shift = moved - f0
        if np.max(np.abs(shift - np.round(shift))) > tol:
            continue  # this operation does not fix the site
        shift = np.round(shift)
        mapped = lf @ R.T + t - shift
        for m in mapped:
            if np.min(np.max(np.abs(lf - m), axis=1)) > tol:
                return False
    return True


def site_labels(structure: Structure) -> list[str]:
    """VESTA-style labels: element + running number per element (Bi7)."""
    counts: dict[str, int] = {}
    labels = []
    for site in structure:
        el = _element(site)
        counts[el] = counts.get(el, 0) + 1
        labels.append(f"{el}{counts[el]}")
    return labels


# ---------------------------------------------------------------- workflow: find all polyhedra, then analyse


@dataclass
class PolyhedronType:
    type_id: str
    kind: str  # "coordination" (chemical bonds to counter-ions) or "element_shell" (nearest shell of one element)
    center_element: str
    representative_index: int
    wyckoff: str
    site_symmetry: str
    ligand_elements: list[str]
    ligand_formula: str
    instances: list[int] = field(default_factory=list)  # indices into the polyhedra list
    also: list[str] = field(default_factory=list)  # other ways the same polyhedron was found


def compute_metrics(center: np.ndarray, center_element: str, ligs: list[Ligand], anion_center: bool) -> tuple[dict, list[list[int]]]:
    pts = np.array([l.cart for l in ligs])
    lengths = np.array([l.distance for l in ligs])
    econ, lw = effective_coordination_number(lengths)
    h = hull(pts)
    shape, face_sizes = identify_shape(pts)
    qe, bav = quadratic_elongation_and_bav(center, pts, shape)
    metrics = {
        "coordination_number": len(ligs),
        "shape": shape,
        "face_sizes": face_sizes,
        "average_bond_length": average_bond_length(lengths),
        "min_bond_length": float(lengths.min()),
        "max_bond_length": float(lengths.max()),
        "polyhedral_volume": float(h.volume) if h is not None else None,
        "distortion_index": distortion_index(lengths),
        "effective_coordination_number": econ,
        "weighted_average_bond_length": lw,
        "quadratic_elongation": qe,
        "bond_angle_variance": bav,
        "bond_valence_sum": bond_valence_sum(center_element, ligs, anion_center=anion_center),
        "center_offset": float(np.linalg.norm(center - pts.mean(axis=0))),
    }
    faces = [[int(x) for x in s] for s in h.simplices] if h is not None else []
    return metrics, faces


def _formula(ligs: list[Ligand]) -> str:
    counts: dict[str, int] = {}
    for l in ligs:
        counts[l.element] = counts.get(l.element, 0) + 1
    return "".join(f"{el}{n}" for el, n in sorted(counts.items()))


@dataclass
class SiteEnvironment:
    """Step 1 result for one symmetry-distinct site: its coordination environment and whether it can be a polyhedron centre."""

    representative_index: int
    element: str
    wyckoff: str
    site_symmetry: str
    orbit: list[int]
    oxidation_state: float | None
    ligand_elements: list[str]
    coordination_number: int
    ligand_formula: str
    shape: str
    forms_polyhedron: bool
    reason: str
    shannon: dict | None = None
    type_ids: list[str] = field(default_factory=list)
    symmetry_consistent: bool | None = None
    ligands: list = field(default_factory=list, repr=False)


def polyhedron_feasibility(center: np.ndarray, ligs: list[Ligand]) -> tuple[bool, str]:
    """A position can host a polyhedron if ≥4 ligands enclose a volume with the centre inside."""
    if len(ligs) < 4:
        return False, f"only {len(ligs)} ligand(s): too few to enclose a volume"
    pts = np.array([l.cart for l in ligs])
    h = hull(pts)
    if h is None:
        return False, "ligands are coplanar (no enclosed volume)"
    if len(h.vertices) < len(ligs):
        return False, f"{len(ligs) - len(h.vertices)} ligand(s) lie inside the hull of the others"
    if np.any(h.equations[:, :3] @ center + h.equations[:, 3] > 1e-6):
        return False, "centre lies outside its ligand hull (one-sided coordination)"
    return True, "ok"


def analyze(
    structure: Structure,
    centers: list[str] | None = None,
    ligands: list[str] | None = None,
    method: str = "shannon",
    max_bond: float | None = None,
    min_bond: float = 0.0,
    radius_tolerance: float = 0.15,
    include_element_shells: bool = False,
    all_instances: bool = True,
) -> tuple[list[PolyhedronResult], list[PolyhedronType], list[SiteEnvironment], SiteSymmetryInfo, dict]:
    """Workflow: coordination environment of every Wyckoff site → which sites form polyhedra → analyses.

    1. Symmetry: group sites into Wyckoff orbits (spglib).
    2. Coordination environment of each orbit's representative, bonding to the counter-ions
       (anions around cations, cations around anions), with ``method``:
         * ``shannon`` (default) – self-consistent Shannon (1976) radii, see :func:`shannon_shell`
         * ``crystalnn`` – Voronoi solid angle + electronegativity (Pan et al. 2021)
         * ``cutoff``    – min_bond ≤ d ≤ max_bond
    3. Feasibility: the site is a polyhedron centre if ≥4 ligands enclose a volume with the
       centre inside (:func:`polyhedron_feasibility`).
    4. Analyses on every feasible polyhedron, built around all symmetry-equivalent sites.
    Optionally (``include_element_shells``) the nearest shell of each element around each site is
    added as further polyhedra (e.g. the Ti8 cube around Sr in SrTiO3).
    """
    if method == "ionic_radii":
        method = "shannon"
    if max_bond is not None and method != "cutoff":
        method = "cutoff"
    if method not in {"shannon", "crystalnn", "cutoff"}:
        raise ValueError("method must be 'shannon', 'crystalnn' or 'cutoff'")
    if method == "cutoff" and max_bond is None:
        raise ValueError("method='cutoff' needs max_bond (Å).")

    present = sorted({_element(s) for s in structure})
    center_elements = centers or present
    oxi = _oxidation_states(structure)
    notes = []
    if method == "shannon" and not oxi:
        method = "crystalnn"
        notes.append("No charge-balanced oxidation states found, so Shannon radii cannot be assigned; used CrystalNN.")
    sym = site_symmetry(structure)
    labels = site_labels(structure)
    cnn = None
    if method == "crystalnn":
        from pymatgen.analysis.local_env import CrystalNN

        cnn = CrystalNN()

    orbits: dict[int, list[int]] = {}
    for i, rep in enumerate(sym.orbit):
        orbits.setdefault(rep, []).append(i)

    def direct(i: int, lig_els: list[str]) -> tuple[list[Ligand], dict | None]:
        if method == "shannon":
            return shannon_shell(structure, i, lig_els, oxi, radius_tolerance)
        return find_ligands(structure, i, lig_els, method, max_bond, min_bond, radius_tolerance, oxi, cnn), None

    # Bonds are found from the cation side and then read backwards for the anions, so the bond
    # graph is symmetric: every bond counted at a cation is counted at its anion (bond-count
    # conservation: Σ_cations m·CN = Σ_anions m·CN).
    use_graph = bool(oxi) and not ligands
    cation_bonds: dict[int, tuple[list[Ligand], dict | None]] = {}
    anion_bonds: dict[int, list[Ligand]] = {}
    if use_graph:
        lat = structure.lattice.matrix
        for i, site in enumerate(structure):
            el = _element(site)
            if oxi.get(el, 0) <= 0:
                continue
            try:
                cation_bonds[i] = direct(i, counter_ions(el, present, oxi))
            except ValueError as exc:
                cation_bonds[i] = ([], None)
                notes.append(f"{labels[i]}: {exc}")
            for l in cation_bonds[i][0]:
                img = np.array(l.image)
                cart = site.coords - img @ lat
                anion_bonds.setdefault(l.index, []).append(
                    Ligand(i, el, tuple(int(x) for x in -img), cart, l.distance)
                )

    def anion_info(i: int, ligs: list[Ligand]) -> dict | None:
        el = _element(structure[i])
        radii = shannon_radii(el, oxi.get(el)) if ligs else {}
        # anion radius at its CN, or at the nearest tabulated CN (e.g. O2- has no CN 5 entry)
        ra = radii[min(radii, key=lambda n: (abs(n - len(ligs)), n))] if radii else None
        rl = [ligand_radius(l.element, oxi.get(l.element)) for l in ligs]
        if ra is None or None in rl or not rl:
            return None
        mean_rl = float(np.mean(rl))
        return {"shannon_cn": len(ligs), "shannon_consistent": True, "center_radius": ra, "ligand_radius": mean_rl,
                "expected_bond_length": ra + mean_rl, "radius_ratio": mean_rl / ra}

    def coordination(i: int, lig_els: list[str]) -> tuple[list[Ligand], dict | None]:
        if use_graph:
            if i in cation_bonds:
                return cation_bonds[i]
            if oxi.get(_element(structure[i]), 0) < 0:
                ligs = sorted(anion_bonds.get(i, []), key=lambda l: l.distance)
                return ligs, anion_info(i, ligs) if method == "shannon" else None
        return direct(i, lig_els)

    results: list[PolyhedronResult] = []
    types: list[PolyhedronType] = []
    environments: list[SiteEnvironment] = []

    def add_type(rep, members, kind, ligs, rebuild, anion_center, env):
        formula = _formula(ligs)
        t = PolyhedronType(
            type_id=f"{labels[rep]}-{formula}" + ("" if kind == "coordination" else "-shell"),
            kind=kind,
            center_element=_element(structure[rep]),
            representative_index=rep,
            wyckoff=sym.wyckoff[rep],
            site_symmetry=sym.site_symmetry[rep],
            ligand_elements=sorted({l.element for l in ligs}),
            ligand_formula=formula,
        )
        types.append(t)
        env.type_ids.append(t.type_id)
        for m in members if all_instances else [rep]:
            mligs = ligs if m == rep else rebuild(m)
            if not mligs:
                continue
            site = structure[m]
            metrics, faces = compute_metrics(site.coords, t.center_element, mligs, anion_center)
            t.instances.append(len(results))
            results.append(PolyhedronResult(m, t.center_element, site.coords.copy(), mligs, metrics, faces, type_id=t.type_id))
        return t

    for rep, members in orbits.items():
        el = _element(structure[rep])
        if el not in center_elements:
            continue
        anion_center = oxi.get(el, 1) < 0
        lig_els = list(ligands) if ligands else counter_ions(el, present, oxi)
        try:
            ligs, info = coordination(rep, lig_els)
        except ValueError as exc:
            ligs, info = [], None
            notes.append(f"{labels[rep]}: {exc}")
        ok, reason = polyhedron_feasibility(structure[rep].coords, ligs) if ligs else (False, "no ligands found")
        shape = identify_shape(np.array([l.cart for l in ligs]))[0] if ligs else "none"
        if info and info.get("radius_ratio"):
            info["pauling_cn"] = pauling_cn(info["radius_ratio"])
        env = SiteEnvironment(
            representative_index=rep,
            element=el,
            wyckoff=sym.wyckoff[rep],
            site_symmetry=sym.site_symmetry[rep],
            orbit=members,
            oxidation_state=oxi.get(el),
            ligand_elements=lig_els,
            coordination_number=len(ligs),
            ligand_formula=_formula(ligs) if ligs else "",
            shape=shape,
            forms_polyhedron=ok,
            reason=reason,
            shannon=info,
            ligands=ligs,
        )
        environments.append(env)
        seen = set()
        if ok:
            add_type(rep, members, "coordination", ligs, lambda m, le=lig_els: coordination(m, le)[0], anion_center, env)
            seen.add(frozenset((l.index, l.image) for l in ligs))
        if include_element_shells:
            for e in ligands or present:
                sligs = element_shell(structure, rep, e)
                key = frozenset((l.index, l.image) for l in sligs)
                if not sligs or key in seen or not polyhedron_feasibility(structure[rep].coords, sligs)[0]:
                    continue
                seen.add(key)
                add_type(rep, members, "element_shell", sligs, lambda m, e=e: element_shell(structure, m, e), anion_center, env)

    def family(t: PolyhedronType) -> str:
        if t.kind != "coordination":
            return t.type_id
        return "anion-centred" if oxi.get(t.center_element, 1) < 0 else "cation-centred"

    group_of = {t.type_id: family(t) for t in types}
    groups: dict[str, list[PolyhedronResult]] = {}
    for p in results:
        groups.setdefault(group_of[p.type_id], []).append(p)
    for members in groups.values():
        _connectivity(structure, members)

    settings = {
        "method": method,
        "centers": center_elements,
        "ligands": ligands,
        "max_bond": max_bond,
        "min_bond": min_bond,
        "radius_tolerance": radius_tolerance if method == "shannon" else None,
        "include_element_shells": include_element_shells,
        "oxidation_states": oxi or None,
        "notes": notes,
    }
    for env in environments:
        env.symmetry_consistent = symmetry_consistent(structure, sym, env.representative_index, env.ligands) if env.ligands else None
    return results, types, environments, sym, settings


def _connectivity(structure: Structure, results: list[PolyhedronResult]) -> None:
    """Corner/edge/face sharing between polyhedra of one family, including periodic images."""
    if not results:
        return
    by_index = {p.center_index: p for p in results}
    keys = {p.center_index: {(l.index, l.image) for l in p.ligands} for p in results}
    reach = 2.0 * max(l.distance for p in results for l in p.ligands) + 0.01
    kinds = {1: "corner", 2: "edge"}
    for p in results:
        for nn in structure.get_neighbors(structure[p.center_index], reach):
            q = by_index.get(int(nn.index))
            if q is None:
                continue
            t = np.array(nn.image, dtype=int)
            shifted = {(j, tuple(int(x) for x in np.array(img) + t)) for j, img in keys[q.center_index]}
            shared = keys[p.center_index] & shifted
            if not shared:
                continue
            lig_cart = {(l.index, l.image): l.cart for l in p.ligands}
            angles = [angle_deg(p.center_cart, lig_cart[k], nn.coords) for k in sorted(shared)]
            p.connections.append(
                {
                    "neighbor_index": q.center_index,
                    "neighbor_element": q.center_element,
                    "neighbor_type_id": q.type_id,
                    "image": [int(x) for x in nn.image],
                    "center_distance": float(nn.nn_distance),
                    "shared_ligands": len(shared),
                    "sharing": kinds.get(len(shared), "face"),
                    "bridging_angles": angles,
                }
            )
