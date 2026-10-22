import math

import numpy as np
import pytest

from crystal_genui import polyhedra as poly
from crystal_genui.core import analyze_spec


def _ligands(center, points):
    return [poly.Ligand(i, "O", (0, 0, 0), np.array(p, float), float(np.linalg.norm(np.array(p) - center))) for i, p in enumerate(points)]


def test_matches_vesta_screenshot_bi7():
    """Bi7 in the VESTA screenshot: one Bi–Se bond of 5.35195 Å and three of 5.51603 Å."""
    lengths = np.array([5.35195, 5.51603, 5.51603, 5.51603])
    assert poly.average_bond_length(lengths) == pytest.approx(5.4750, abs=5e-5)
    assert poly.distortion_index(lengths) == pytest.approx(0.01124, abs=5e-6)
    econ, _ = poly.effective_coordination_number(lengths)
    assert econ == pytest.approx(3.9737, abs=5e-5)


def test_regular_octahedron():
    l = 2.0
    pts = np.array([[l, 0, 0], [-l, 0, 0], [0, l, 0], [0, -l, 0], [0, 0, l], [0, 0, -l]])
    lengths = np.linalg.norm(pts, axis=1)
    assert poly.distortion_index(lengths) == pytest.approx(0)
    assert poly.effective_coordination_number(lengths)[0] == pytest.approx(6)
    assert poly.polyhedral_volume(pts) == pytest.approx(4 / 3 * l**3)
    qe, bav = poly.quadratic_elongation_and_bav(np.zeros(3), pts)
    assert qe == pytest.approx(1)
    assert bav == pytest.approx(0, abs=1e-9)


def test_regular_tetrahedron():
    l = 1.6
    pts = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]]) * l / math.sqrt(3)
    assert poly.polyhedral_volume(pts) == pytest.approx(8 / (9 * math.sqrt(3)) * l**3)
    qe, bav = poly.quadratic_elongation_and_bav(np.zeros(3), pts)
    assert qe == pytest.approx(1)
    assert bav == pytest.approx(0, abs=1e-9)


def test_elongated_octahedron_robinson():
    """Tetragonally elongated octahedron: <λ> > 1, σ² = 0 because all cis angles stay 90°."""
    pts = np.array([[2, 0, 0], [-2, 0, 0], [0, 2, 0], [0, -2, 0], [0, 0, 2.3], [0, 0, -2.3]], float)
    qe, bav = poly.quadratic_elongation_and_bav(np.zeros(3), pts)
    l0 = (poly.polyhedral_volume(pts) / (4 / 3)) ** (1 / 3)
    assert qe == pytest.approx(np.mean((np.linalg.norm(pts, axis=1) / l0) ** 2))
    assert qe > 1
    assert bav == pytest.approx(0, abs=1e-9)


def test_trigonal_prism_has_no_octahedral_bav():
    tri = [[1, 0], [-0.5, math.sqrt(3) / 2], [-0.5, -math.sqrt(3) / 2]]
    pts = np.array([[x, y, z] for z in (1.0, -1.0) for x, y in tri]) * 1.4
    qe, bav = poly.quadratic_elongation_and_bav(np.zeros(3), pts)
    assert qe is None and bav is None


def test_bond_valence_ti_o():
    pts = [[1.9525, 0, 0], [-1.9525, 0, 0], [0, 1.9525, 0], [0, -1.9525, 0], [0, 0, 1.9525], [0, 0, -1.9525]]
    bvs = poly.bond_valence_sum("Ti", _ligands(np.zeros(3), pts))
    assert bvs == pytest.approx(6 * math.exp((1.815 - 1.9525) / 0.37))


def _types(r):
    return {t.type_id: t for t in r.polyhedron_types}


def test_shape_identification():
    tri = [[1, 0], [-0.5, math.sqrt(3) / 2], [-0.5, -math.sqrt(3) / 2]]
    prism = np.array([[x, y, z] for z in (1.0, -1.0) for x, y in tri]) * 1.4
    octa = np.array([[2, 0, 0], [-2, 0, 0], [0, 2, 0], [0, -2, 0], [0, 0, 2], [0, 0, -2]], float)
    cube = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    assert poly.identify_shape(prism)[0] == "trigonal prism"
    assert poly.identify_shape(octa)[0] == "octahedron"
    assert poly.identify_shape(cube)[0] == "cube"


def test_shannon_radii_match_shannon_1976():
    assert poly.shannon_radii("Ti", 4)[6] == pytest.approx(0.605)
    assert poly.shannon_radii("O", -2)[6] == pytest.approx(1.40)
    assert poly.shannon_radii("Sr", 2)[12] == pytest.approx(1.44)
    assert poly.pauling_cn(0.605 / 1.40) == 6


def test_srtio3_workflow():
    r = analyze_spec("SrTiO3")
    env = {e.site_label: e for e in r.site_environments}
    assert (env["Sr1"].wyckoff, env["Sr1"].coordination_number, env["Sr1"].ligand_formula) == ("1a", 12, "O12")
    assert (env["Ti1"].wyckoff, env["Ti1"].coordination_number, env["Ti1"].shape) == ("1b", 6, "octahedron")
    # the anion is a polyhedron centre too: O at 3c sits in an OSr4Ti2 octahedron
    assert (env["O1"].wyckoff, env["O1"].ligand_formula, env["O1"].shape) == ("3c", "Sr4Ti2", "octahedron")
    assert all(e.forms_polyhedron and e.shannon.shannon_consistent for e in env.values())
    t = _types(r)
    ti = t["Ti1-O6"]
    assert ti.metrics.polyhedral_volume == pytest.approx(4 / 3 * (3.905 / 2) ** 3, rel=1e-5)
    assert ti.metrics.distortion_index == pytest.approx(0, abs=1e-6)
    assert ti.connectivity == {"corner": 6.0} and ti.mean_bridging_angle == pytest.approx(180)
    assert t["O1-Sr4Ti2"].n_instances == 3
    cc = r.crystal_chemistry
    assert cc.bond_count_balance["balanced"] and cc.site_symmetry_consistent
    assert all(x["agrees"] for x in cc.pauling_rule_1_radius_ratio)
    assert all(x["satisfied"] for x in cc.pauling_rule_2_electrostatic_valence)
    assert cc.global_instability_index < 0.15


def test_catio3_octahedral_tilt_and_two_oxygen_sites():
    r = analyze_spec("CaTiO3")
    t = _types(r)
    assert t["Ti1-O6"].connectivity == {"corner": 6.0}
    assert 150 < t["Ti1-O6"].mean_bridging_angle < 160  # Ti–O–Ti ≈ 156° in orthorhombic CaTiO3
    o_sites = sorted(e.wyckoff for e in r.site_environments if e.element == "O")
    assert o_sites == ["4c", "8d"]
    assert r.crystal_chemistry.global_instability_index < 0.15


def test_batio3_ti_off_centering():
    r = analyze_spec("BaTiO3")
    ti = _types(r)["Ti1-O6"]
    assert ti.metrics.center_offset > 0.1
    assert ti.metrics.distortion_index > 0.01


@pytest.mark.parametrize("method", ["shannon", "crystalnn"])
def test_bi2se3_octahedra(method):
    r = analyze_spec("Bi2Se3", method=method)
    bi = [p for p in r.polyhedra if p.center_element == "Bi"]
    assert len(bi) == 6 and all(p.metrics.coordination_number == 6 for p in bi)
    d = sorted(l.distance for l in bi[0].ligands)
    assert d[0] == pytest.approx(2.851, abs=0.01) and d[-1] == pytest.approx(3.075, abs=0.01)


def test_bi2se3_outer_se_cannot_form_polyhedron():
    """Se at 6c bonds to only three Bi (the van der Waals gap is on its other side)."""
    r = analyze_spec("Bi2Se3")
    se = next(e for e in r.site_environments if e.element == "Se" and e.wyckoff == "6c")
    assert se.coordination_number == 3 and not se.forms_polyhedron
    assert any(x["site"] == "Se 6c" and not x["satisfied"] for x in r.crystal_chemistry.pauling_rule_2_electrostatic_valence)


def test_element_shells():
    r = analyze_spec("SrTiO3", include_element_shells=True)
    t = _types(r)
    assert t["Sr1-Ti8-shell"].metrics.shape == "cube"
    assert t["Ti1-Ti6-shell"].metrics.shape == "octahedron"


def test_cutoff_method():
    r = analyze_spec("SrTiO3", centers=["Ti"], max_bond=2.2)
    assert r.settings.method == "cutoff"
    assert [p.metrics.coordination_number for p in r.polyhedra] == [6]


def test_ionic_radius_per_site_for_any_method():
    """Atom sizes in the viewer: Shannon radius at the site's charge and observed CN."""
    for method in ("shannon", "crystalnn"):
        env = {e.site_label: e for e in analyze_spec("CaTiO3", method=method).site_environments}
        assert (env["Ca1"].ionic_radius, env["Ca1"].ionic_radius_cn) == (1.12, 8)
        assert (env["Ti1"].ionic_radius, env["Ti1"].ionic_radius_cn) == (0.605, 6)
        assert (env["O1"].ionic_radius, env["O1"].ionic_radius_cn) == (1.38, 4)
        assert env["O5"].ionic_radius_cn == 4  # CN 5 is not tabulated for O2-; nearest lower CN
