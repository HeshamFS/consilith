"""HTML and SVG pieces of the crystal report, in the Consilith palette, light and dark.

Pure Python on the polyhedra report as plain JSON: no plotting library, no network, no
third-party import. The notebook only arranges what these functions return, so they are tested
without marimo. The colours come from ``report_palette`` and follow the host's theme (see
``report_head.html``) or, with no host, the system's.
"""

from __future__ import annotations

import re

from html import escape
from math import ceil, floor, log10
from typing import Any

from . import charts
from .report_palette import DARK, FONT_DISPLAY, FONT_MONO, FONT_UI, LIGHT, SERIES, variables

WIDTH, HEIGHT = 720, 300
MARGIN = {"left": 54, "right": 14, "top": 14, "bottom": 52}
ANGSTROM = "Å"

TABLE_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    # (key, header, unit, kind)
    ("type_id", "Type", "", "text"),
    ("ligand_formula", "Ligands", "", "text"),
    ("coordination_number", "CN", "", "num"),
    ("shape", "Shape", "", "text"),
    ("n_instances", "In cell", "", "num"),
    ("average_bond_length", "Mean bond", ANGSTROM, "num"),
    ("polyhedral_volume", "Volume", f"{ANGSTROM}³", "num"),
    ("distortion_index", "Distortion", "", "num"),
    ("effective_coordination_number", "ECoN", "", "num"),
    ("quadratic_elongation", "Quad. elong.", "", "num"),
    ("bond_valence_sum", "BVS", "v.u.", "num"),
)


def sig(value: float | int | None, digits: int = 4) -> str:
    """A number as it reads in a report: few significant digits, no trailing zeros."""
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    text = f"{value:.{digits}g}"
    return text.replace("e+0", "e").replace("e-0", "e-") if "e" in text else text


def stylesheet() -> str:
    return f"""
<style>
:root {{ {variables(LIGHT)} color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ {variables(DARK)} color-scheme: dark; }} }}
:root[data-theme="dark"] {{ {variables(DARK)} color-scheme: dark; }}
:root[data-theme="light"] {{ {variables(LIGHT)} color-scheme: light; }}
html, body, #root, #root > div, #root > div > div {{ background: var(--bg) !important; }}
body {{ margin: 0; }}
.cr {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 56px; color: var(--ink);
  font-family: {FONT_UI}; font-size: 15px; line-height: 1.55; }}
.cr * {{ box-sizing: border-box; }}
.cr .eyebrow {{ font-size: 12px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--ink-3); font-weight: 600; }}
.cr h1 {{ font-family: {FONT_DISPLAY}; font-weight: 400; font-size: 44px; line-height: 1.08;
  margin: 8px 0 8px; letter-spacing: -.01em; color: var(--ink); }}
.cr h2 {{ font-family: {FONT_DISPLAY}; font-weight: 400; font-size: 27px; line-height: 1.15;
  margin: 40px 0 6px; color: var(--ink); }}
.cr .lede {{ color: var(--ink-2); font-size: 16.5px; max-width: 62ch; margin: 0 0 16px; }}
.cr .note {{ background: var(--warn-bg); color: var(--warn-fg); border-radius: 12px;
  padding: 11px 15px; font-size: 14px; margin: 0 0 22px; }}
.cr .note b {{ font-weight: 650; }}
.cr .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
.cr .kpi {{ background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  padding: 14px 16px 12px; }}
.cr .kpi .k {{ font-size: 12px; color: var(--ink-3); font-weight: 600; letter-spacing: .02em; }}
.cr .kpi .v {{ font-family: {FONT_MONO}; font-size: 21px; margin-top: 5px; color: var(--ink);
  letter-spacing: -.01em; overflow-wrap: anywhere; }}
.cr .kpi .d {{ font-size: 12.5px; color: var(--ink-2); margin-top: 2px; }}
.cr .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
.cr .chips span {{ font-family: {FONT_MONO}; font-size: 12px; background: var(--surface-2);
  border: 1px solid var(--line); border-radius: 999px; padding: 3px 10px; color: var(--ink-2); }}
.cr .card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  padding: 16px 18px 12px; margin: 12px 0; }}
.cr .card h3 {{ font-size: 15px; margin: 0; font-weight: 650; color: var(--ink); }}
.cr .card p.cap {{ margin: 2px 0 8px; font-size: 13px; color: var(--ink-3); }}
.cr svg {{ display: block; width: 100%; height: auto; }}
.cr .legend {{ display: flex; gap: 14px; flex-wrap: wrap; font-size: 12.5px; color: var(--ink-2);
  margin: 2px 0 6px; }}
.cr .legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  vertical-align: baseline; margin-right: 6px; }}
.cr .tablewrap {{ background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  overflow: hidden; margin: 10px 0; }}
.cr .scroll {{ max-height: 420px; overflow: auto; }}
.cr table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
.cr th {{ position: sticky; top: 0; background: var(--surface-2); text-align: right;
  color: var(--ink-2); font-weight: 600; padding: 9px 12px; white-space: nowrap;
  border-bottom: 1px solid var(--line-strong); }}
.cr th small {{ display: block; font-weight: 500; color: var(--ink-3); font-size: 11.5px; }}
.cr td {{ text-align: right; padding: 7px 12px; border-bottom: 1px solid var(--line);
  font-family: {FONT_MONO}; font-size: 12.5px; color: var(--ink); white-space: nowrap; }}
.cr th.t, .cr td.t {{ text-align: left; }}
.cr tr:last-child td {{ border-bottom: 0; }}
.cr .foot {{ margin-top: 30px; padding-top: 14px; border-top: 1px solid var(--line);
  color: var(--ink-3); font-size: 12.5px; }}
.cr svg text {{ font-family: {FONT_MONO}; }}
.cr svg .axis {{ font-family: {FONT_UI}; }}
{series_css()}
@media (max-width: 640px) {{
  .cr {{ padding: 20px 14px 40px; }}
  .cr h1 {{ font-size: 34px; }}
  .cr .kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .cr .kpi .v {{ font-size: 18px; }}
}}
</style>"""


def series_css() -> str:
    rules = []
    for i, name in enumerate(SERIES):
        rules.append(
            f".cr .s{i} {{ fill: var(--{name}); }} .cr .l{i} {{ background: var(--{name}); }}"
        )
    return "\n".join(rules)


def nice_ticks(low: float, high: float, target: int = 5) -> list[float]:
    """Round tick values from ``low`` to at least ``high``."""
    if high <= low:
        high = low + 1.0
    raw = (high - low) / max(1, target)
    magnitude = 10 ** floor(log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    ticks, value = [], floor(low / step) * step
    while value <= high + step * 0.999 and len(ticks) < 20:
        ticks.append(round(value, 12))
        value += step
        if ticks[-1] >= high:
            break
    return ticks


def bar_chart(chart: dict[str, Any], species: list[str] | None = None) -> str:
    """One responsive SVG grouped bar chart of a ``ChartData`` with ``kind: "bar"``. With
    ``species`` a series named after an element (or a pair, by its centre) takes that element's
    colour, the one the 3D viewer gives it."""
    series = [s for s in chart["series"] if s["x"]]
    if not series:
        return ""
    cats: list[str] = []
    for s in series:
        for x in s["x"]:
            if str(x) not in cats:
                cats.append(str(x))
    left, right, top, bottom = (MARGIN[k] for k in ("left", "right", "top", "bottom"))
    inner_w, inner_h = WIDTH - left - right, HEIGHT - top - bottom
    top_value = max([v for s in series for v in s["y"]] + [1])
    ticks = [t for t in nice_ticks(0, top_value, 4)]
    y_max = max(ticks[-1], top_value)
    group = inner_w / len(cats)
    gap = min(12.0, group * 0.22)
    bar_w = (group - gap) / len(series)
    description = f"{chart['title']}: {chart['yLabel']} by {chart['xLabel'].lower()}"
    out = [
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(description)}">',
        f"<title>{escape(description)}</title>",
    ]

    def py(v: float) -> float:
        return top + (1 - v / y_max) * inner_h

    for t in ticks:
        out.append(
            f'<line x1="{left}" x2="{WIDTH - right}" y1="{py(t):.1f}" y2="{py(t):.1f}" '
            f'stroke="var(--line)" stroke-width="1"/>'
            f'<text x="{left - 8}" y="{py(t) + 4:.1f}" text-anchor="end" font-size="11" '
            f'fill="var(--ink-3)">{escape(sig(t, 3))}</text>'
        )
    stride = max(1, ceil(len(cats) * 34 / inner_w))
    for ci, cat in enumerate(cats):
        cx = left + (ci + 0.5) * group
        if ci % stride == 0:
            out.append(
                f'<text x="{cx:.1f}" y="{top + inner_h + 17}" text-anchor="middle" '
                f'font-size="11" fill="var(--ink-3)">{escape(cat)}</text>'
            )
        for si, s in enumerate(series):
            if cat not in [str(x) for x in s["x"]]:
                continue
            v = s["y"][[str(x) for x in s["x"]].index(cat)]
            x0 = cx - group / 2 + gap / 2 + si * bar_w
            if v > 0:
                height = max(2.0, (v / y_max) * inner_h)
                out.append(
                    f'<rect class="s{colour_index(s["name"], si, species)}" x="{x0 + 1:.1f}" '
                    f'y="{top + inner_h - height:.1f}" width="{max(2.0, bar_w - 2):.1f}" '
                    f'height="{height:.1f}" rx="3"><title>{escape(s["name"])}, {escape(cat)}: '
                    f"{v}</title></rect>"
                )
                if len(cats) * len(series) <= 24:
                    out.append(
                        f'<text x="{x0 + bar_w / 2:.1f}" y="{top + inner_h - height - 5:.1f}" '
                        f'text-anchor="middle" font-size="11" fill="var(--ink-2)">{v}</text>'
                    )
    out.append(
        f'<line x1="{left}" x2="{WIDTH - right}" y1="{top + inner_h}" y2="{top + inner_h}" '
        f'stroke="var(--line-strong)"/>'
        f'<text class="axis" x="{left + inner_w / 2:.1f}" y="{HEIGHT - 8}" text-anchor="middle" '
        f'font-size="12" fill="var(--ink-2)">{escape(chart["xLabel"])}</text>'
        f'<text class="axis" transform="translate(13 {top + inner_h / 2:.1f}) rotate(-90)" '
        f'text-anchor="middle" font-size="12" fill="var(--ink-2)">{escape(chart["yLabel"])}</text>'
    )
    out.append("</svg>")
    return "".join(out)


def colour_index(name: str, position: int, species: list[str] | None) -> int:
    """The series colour: the element's place among the structure's sorted species, as in the
    viewer (a pair such as ``Bi–Se`` goes by its centre); else the series' own position."""
    centre = re.split(r"[–-]", str(name), maxsplit=1)[0].strip()
    if species and len(species) <= len(SERIES) and centre in species:
        return species.index(centre)
    return position % len(SERIES)


def species_of(report: dict[str, Any]) -> list[str]:
    """The structure's elements sorted by symbol: the order the viewer colours them in."""
    sites = report.get("structure", {}).get("sites", [])
    return sorted({str(s["element"]) for s in sites if s.get("element")})


def legend(chart: dict[str, Any], species: list[str] | None = None) -> str:
    if len(chart["series"]) < 2:
        return ""
    chips = "".join(
        f'<span><i class="l{colour_index(s["name"], i, species)}"></i>{escape(s["name"])}</span>'
        for i, s in enumerate(chart["series"])
    )
    return f'<div class="legend">{chips}</div>'


def _card(title: str, caption: str, body: str, legend_html: str = "") -> str:
    return (
        f'<div class="card"><h3>{escape(title)}</h3><p class="cap">{escape(caption)}</p>'
        f"{legend_html}{body}</div>"
    )


def _kpi(label: str, value: str, detail: str = "") -> str:
    extra = f'<div class="d">{escape(detail)}</div>' if detail else ""
    return (
        f'<div class="kpi"><div class="k">{escape(label)}</div>'
        f'<div class="v">{escape(value)}</div>{extra}</div>'
    )


def header(report: dict[str, Any]) -> str:
    st = report["structure"]
    source = st["source"]
    lede = (
        f"{st['space_group']['symbol']} (No. {st['space_group']['number']}, "
        f"{st['space_group']['crystal_system']}), {st['n_sites']} sites in the cell, "
        f"{len(report['polyhedra'])} coordination polyhedra."
    )
    note = ""
    if source.get("kind") == "example":
        note = (
            '<div class="note"><b>Bundled example.</b> It is an approximate structure, not a '
            "DFT-relaxed prediction. Coordination depends on the neighbour method below.</div>"
        )
    return (
        '<div class="cr"><div class="eyebrow">Consilith &middot; Crystal structure and coordination</div>'
        f"<h1>{escape(st['reduced_formula'])}</h1>"
        f'<p class="lede">{escape(lede)}</p>{note}'
    )


def summary(report: dict[str, Any]) -> str:
    st = report["structure"]
    lat = st["lattice"]
    gii = report.get("crystal_chemistry", {}).get("global_instability_index")
    tiles = [
        _kpi("Space group", st["space_group"]["symbol"], f"No. {st['space_group']['number']}"),
        _kpi("Cell volume", f"{sig(lat['volume'])} {ANGSTROM}³", f"{st['n_sites']} sites"),
        _kpi("Density", f"{sig(st['density'])} g/cm³", st["reduced_formula"]),
        _kpi(
            "Instability index",
            sig(gii) if gii is not None else "—",
            "bond-valence, v.u.",
        ),
    ]
    cell = "".join(
        f"<span>{name} = {sig(lat[key])}{unit}</span>"
        for name, key, unit in (
            ("a", "a", f" {ANGSTROM}"),
            ("b", "b", f" {ANGSTROM}"),
            ("c", "c", f" {ANGSTROM}"),
            ("α", "alpha", "°"),
            ("β", "beta", "°"),
            ("γ", "gamma", "°"),
        )
    )
    s = report["settings"]
    method = f"method = {s['method']}"
    if s.get("max_bond") is not None:
        method += f", max bond = {sig(s['max_bond'])} {ANGSTROM}"
    if s.get("radius_tolerance") is not None:
        method += f", radius tolerance = {sig(s['radius_tolerance'])}"
    return (
        f'<div class="kpis">{"".join(tiles)}</div>'
        f'<div class="chips">{cell}<span>{escape(method)}</span></div>'
    )


def table_html(report: dict[str, Any]) -> str:
    types = [t for t in report["polyhedron_types"] if t.get("kind") != "element_shell"]
    head = "".join(
        f'<th class="{"t" if kind == "text" else ""}">{escape(label)}'
        + (f"<small>{escape(unit)}</small>" if unit else "")
        + "</th>"
        for _key, label, unit, kind in TABLE_COLUMNS
    )

    def cell(t: dict[str, Any], key: str) -> object:
        return (
            t.get(key)
            if key in ("type_id", "ligand_formula", "n_instances")
            else (t["metrics"].get(key))
        )

    rows = []
    for t in types:
        cells = []
        for key, _label, _unit, kind in TABLE_COLUMNS:
            v = cell(t, key)
            text = escape(str(v)) if kind == "text" else escape(sig(v))
            cells.append(f'<td class="{"t" if kind == "text" else ""}">{text}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="tablewrap"><div class="scroll"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>"
    )


def sections(report: dict[str, Any]) -> str:
    coordination = charts.coordination_histogram(report)
    bonds = charts.bond_length_distribution(report)
    out = ["<h2>Polyhedra</h2>"]
    out.append(
        '<p class="cap" style="color:var(--ink-3);margin:0 0 4px">One row per symmetry-distinct '
        "polyhedron. Lengths in angstrom, volumes in cubic angstrom.</p>"
    )
    out.append(table_html(report))
    out.append("<h2>Distributions</h2>")
    if coordination:
        chart = coordination["chart"]
        out.append(
            _card(
                "Coordination numbers",
                "Polyhedra in the cell by the number of ligands around their centre.",
                bar_chart(chart, species_of(report)),
                legend(chart, species_of(report)),
            )
        )
    if bonds:
        chart = bonds["chart"]
        extra = (
            f" {chart['omittedSeries']} less common pair(s) are not drawn."
            if chart.get("omittedSeries")
            else ""
        )
        out.append(
            _card(
                "Bond lengths",
                "Centre to ligand distances of the cell, counted once from each cation." + extra,
                bar_chart(chart, species_of(report)),
                legend(chart, species_of(report)),
            )
        )
    return "".join(out)


def footer(report: dict[str, Any]) -> str:
    source = report["structure"]["source"]
    origin = source.get("id") or source.get("kind", "")
    notes = report["settings"].get("notes") or []
    extra = " ".join(escape(n) for n in notes)
    return (
        '<div class="foot">Source: '
        f"{escape(str(origin))}. Coordination follows the selected neighbour method; this report "
        "presents what the analysis computed and does not establish stability or any "
        f"experimental property. {extra}</div></div>"
    )


def page(report: dict[str, Any]) -> str:
    return stylesheet() + header(report) + summary(report) + sections(report) + footer(report)
