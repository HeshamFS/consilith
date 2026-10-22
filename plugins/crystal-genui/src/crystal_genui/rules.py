"""Crystal-chemical rules checked on the found polyhedra.

* Pauling (1929) rule 1 – radius ratio r_c/r_a sets the coordination number
* Pauling rule 2 – electrostatic valence: Σ z_c/CN_c over the cations bonded to an anion ≈ |z_a|
* Pauling rule 3 – corner < edge < face sharing in stability; edge/face sharing reported with M–M distances
* Pauling rule 4 – high-valence, low-CN cations avoid sharing edges/faces with each other
* Pauling rule 5 – parsimony: few distinct environments per element
* Brown's valence-sum rule – bond-valence sums ≈ |oxidation state|, summarised as the global
  instability index GII = sqrt(⟨(BVS − |V|)²⟩) (≲0.1 v.u. typical, >0.2 v.u. suggests strain or a wrong structure)
* Bond-count conservation – Σ_cations m·CN = Σ_anions m·CN
* Site symmetry – every polyhedron is invariant under its centre's site-symmetry group
"""

from __future__ import annotations

import math
from collections import defaultdict

from .polyhedra import PolyhedronResult, PolyhedronType, SiteEnvironment, bond_valence_sum


def _bin(cn: int) -> int:
    return min((2, 3, 4, 6, 8, 12), key=lambda b: abs(b - cn))


def evaluate(
    results: list[PolyhedronResult],
    types: list[PolyhedronType],
    environments: list[SiteEnvironment],
    oxi: dict[str, float] | None,
) -> dict:
    oxi = oxi or {}
    by_type = {t.type_id: t for t in types}
    rep_poly = {}
    for t in types:
        if t.kind == "coordination" and t.instances:
            rep_poly[t.representative_index] = results[t.instances[0]]

    def is_cation(el):
        return oxi.get(el, 0) > 0

    def label(env):
        return f"{env.element} {env.wyckoff}"

    # Rule 1
    rule1 = []
    for env in environments:
        if not is_cation(env.element) or not env.shannon or not env.shannon.get("radius_ratio"):
            continue
        pred = env.shannon["pauling_cn"]
        rule1.append(
            {
                "site": label(env),
                "radius_ratio": round(env.shannon["radius_ratio"], 4),
                "predicted_cn": pred,
                "observed_cn": env.coordination_number,
                "agrees": abs(_bin(env.coordination_number) - pred) <= 2,
            }
        )

    # Rule 2 (needs the cation CN of every bonded cation)
    cn_of_site = {}
    for env in environments:
        for i in env.orbit:
            cn_of_site[i] = env.coordination_number
    rule2 = []
    for env in environments:
        z_a = oxi.get(env.element)
        if z_a is None or z_a >= 0 or not env.ligands:
            continue
        s = 0.0
        for l in env.ligands:
            z_c, cn_c = oxi.get(l.element), cn_of_site.get(l.index)
            if z_c and cn_c:
                s += z_c / cn_c
        rule2.append(
            {
                "site": label(env),
                "sum_of_electrostatic_bond_strengths": round(s, 4),
                "anion_charge": abs(z_a),
                "relative_deviation": round((s - abs(z_a)) / abs(z_a), 4),
                "satisfied": abs(s - abs(z_a)) / abs(z_a) <= 0.2,
            }
        )

    # Rules 3 & 4 (cation-centred coordination polyhedra)
    sharing = defaultdict(lambda: {"corner": 0, "edge": 0, "face": 0})
    rule4 = []
    seen_pairs = set()
    for p in results:
        t = by_type[p.type_id]
        if t.kind != "coordination" or not is_cation(p.center_element):
            continue
        for c in p.connections:
            q = by_type.get(c["neighbor_type_id"])
            if q is None or q.kind != "coordination" or not is_cation(q.center_element):
                continue
            key = tuple(sorted((p.center_element, q.center_element)))
            sharing["-".join(key)][c["sharing"]] += 1
            pair = (min(p.center_index, c["neighbor_index"]), max(p.center_index, c["neighbor_index"]), c["sharing"], round(c["center_distance"], 3))
            if c["sharing"] in ("edge", "face") and pair not in seen_pairs:
                seen_pairs.add(pair)
                z1, z2 = oxi.get(p.center_element, 0), oxi.get(q.center_element, 0)
                if z1 >= 4 and z2 >= 4 and p.metrics["coordination_number"] <= 6 and c["shared_ligands"] >= 2:
                    rule4.append(
                        {
                            "pair": f"{p.center_element}{z1:+g}–{q.center_element}{z2:+g}",
                            "sharing": c["sharing"],
                            "cation_distance": round(c["center_distance"], 4),
                        }
                    )
    rule3 = {k: dict(v) for k, v in sharing.items()}

    # Rule 5
    envs_per_element = defaultdict(set)
    for env in environments:
        if env.forms_polyhedron:
            envs_per_element[env.element].add((env.coordination_number, env.shape))
    rule5 = {el: len(v) for el, v in envs_per_element.items()}

    # Bond-valence sums and GII
    devs = []
    bvs_rows = []
    for env in environments:
        v = oxi.get(env.element)
        bvs = bond_valence_sum(env.element, env.ligands, anion_center=(v or 0) < 0) if env.ligands and v else None
        if bvs is None:
            continue
        devs.extend([(bvs - abs(v)) ** 2] * len(env.orbit))
        bvs_rows.append({"site": label(env), "bvs": round(bvs, 4), "expected": abs(v), "deviation": round(bvs - abs(v), 4)})
    gii = math.sqrt(sum(devs) / len(devs)) if devs else None

    # Bond-count conservation
    cat = sum(len(e.orbit) * e.coordination_number for e in environments if is_cation(e.element))
    an = sum(len(e.orbit) * e.coordination_number for e in environments if oxi.get(e.element, 0) < 0)

    return {
        "pauling_rule_1_radius_ratio": rule1,
        "pauling_rule_2_electrostatic_valence": rule2,
        "pauling_rule_3_polyhedron_sharing": rule3,
        "pauling_rule_4_high_valence_sharing": rule4,
        "pauling_rule_5_parsimony": rule5,
        "bond_valence_sums": bvs_rows,
        "global_instability_index": round(gii, 4) if gii is not None else None,
        "bond_count_balance": {"cation_side": cat, "anion_side": an, "balanced": cat == an} if oxi else None,
        "site_symmetry_consistent": all(e.symmetry_consistent is not False for e in environments),
    }
