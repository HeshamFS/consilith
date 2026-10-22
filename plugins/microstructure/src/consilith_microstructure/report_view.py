"""HTML and SVG pieces of the grain-growth report, in the Consilith palette.

Pure Python and deterministic: no plotting library, no network, no script. The notebook only
arranges what these functions return, so the pieces can be tested without marimo. Colours are CSS
variables: the light (Paper) and dark (Stage) tokens of ``apps/web/src/shared/theme/tokens.css``,
switched by the host's theme (``report_head.html``) or, with no host, the system's.
"""

from html import escape
from math import ceil, floor, log10
from typing import Any

from .run_tables import DISCLAIMER, RunTables, percent, sig

LIGHT = {
    "ink": "#1c1b18",
    "ink-2": "#5b5850",
    "ink-3": "#6b675e",
    "bg": "#fbfaf7",
    "surface": "#ffffff",
    "surface-2": "#f4f2ec",
    "line": "#e2ded5",
    "line-strong": "#cfcac0",
    "blue": "#2a50cc",
    "magenta": "#c4296a",
    "teal": "#0d8089",
    "amber": "#b4530f",
    "violet": "#5b2ea6",
    "warn-bg": "#fbefd5",
    "warn-fg": "#7a4f00",
    "warn-line": "#f0dfb4",
    "row-alt": "#fdfcfa",
}
DARK = {
    "ink": "#f0ede6",
    "ink-2": "#b5b1a7",
    "ink-3": "#a29d91",
    "bg": "#1c1b18",
    "surface": "#2a2a26",
    "surface-2": "#34342f",
    "line": "#3b3b36",
    "line-strong": "#4a4943",
    "blue": "#7c9bff",
    "magenta": "#ff7fb5",
    "teal": "#5fd3d9",
    "amber": "#f5a15c",
    "violet": "#b79cff",
    "warn-bg": "#3a2e14",
    "warn-fg": "#f2c66b",
    "warn-line": "#5a4520",
    "row-alt": "#2e2e2a",
}

INK = "var(--ink)"
INK_2 = "var(--ink-2)"
INK_3 = "var(--ink-3)"
BG = "var(--bg)"
SURFACE = "var(--surface)"
SURFACE_2 = "var(--surface-2)"
LINE = "var(--line)"
LINE_STRONG = "var(--line-strong)"
BLUE = "var(--blue)"
MAGENTA = "var(--magenta)"
TEAL = "var(--teal)"
AMBER = "var(--amber)"
VIOLET = "var(--violet)"
WARN_BG = "var(--warn-bg)"
WARN_FG = "var(--warn-fg)"


def _variables(tokens: dict[str, str]) -> str:
    return "".join(f"--{name}:{value};" for name, value in tokens.items())

FONT_UI = '"Instrument Sans", system-ui, -apple-system, "Segoe UI", sans-serif'
FONT_DISPLAY = '"Instrument Serif", Georgia, "Times New Roman", serif'
FONT_MONO = '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

WIDTH, HEIGHT = 880, 300
MARGIN = {"left": 66, "right": 28, "top": 14, "bottom": 46}
MAX_FAINT_GRAINS = 150


def stylesheet() -> str:
    """Page-level CSS. Scoped under ``.cr`` except the page background."""
    return f"""
<style>
:root {{ {_variables(LIGHT)} color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ {_variables(DARK)} color-scheme: dark; }} }}
:root[data-theme="dark"] {{ {_variables(DARK)} color-scheme: dark; }}
:root[data-theme="light"] {{ {_variables(LIGHT)} color-scheme: light; }}
html, body, #root, #root > div, #root > div > div {{ background: {BG} !important; }}
body {{ margin: 0; }}
.cr {{ max-width: 980px; margin: 0 auto; padding: 40px 24px 72px; color: {INK};
  font-family: {FONT_UI}; font-size: 15px; line-height: 1.55; }}
.cr * {{ box-sizing: border-box; }}
.cr .eyebrow {{ font-size: 12px; letter-spacing: .14em; text-transform: uppercase;
  color: {INK_3}; font-weight: 600; }}
.cr h1 {{ font-family: {FONT_DISPLAY}; font-weight: 400; font-size: 46px; line-height: 1.08;
  margin: 8px 0 10px; letter-spacing: -.01em; color: {INK}; }}
.cr .intro {{ color: {INK_2}; font-size: 15px; line-height: 1.6; max-width: 70ch; margin: 0 0 12px; }}
.cr .refsTitle {{ margin: 14px 0 6px; font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: {INK_3}; }}
.cr .refs {{ margin: 0 0 22px; padding-left: 20px; color: {INK}; font-size: 14px; line-height: 1.55; }}
.cr .refs li {{ margin: 4px 0; }}
.cr .refs a {{ color: {BLUE}; word-break: break-all; }}
.cr h2 {{ font-family: {FONT_DISPLAY}; font-weight: 400; font-size: 28px; line-height: 1.15;
  margin: 44px 0 6px; color: {INK}; }}
.cr .lede {{ color: {INK_2}; font-size: 17px; max-width: 62ch; margin: 0 0 18px; }}
.cr .note {{ background: {WARN_BG}; color: {WARN_FG}; border-radius: 12px; padding: 12px 16px;
  font-size: 14px; margin: 0 0 26px; border: 1px solid var(--warn-line); }}
.cr .note b {{ font-weight: 650; }}
.cr .kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
.cr .kpi {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px;
  padding: 16px 18px 14px; }}
.cr .kpi .k {{ font-size: 12px; color: {INK_3}; font-weight: 600; letter-spacing: .02em; }}
.cr .kpi .v {{ font-family: {FONT_MONO}; font-size: 22px; margin-top: 6px; color: {INK};
  letter-spacing: -.01em; }}
.cr .kpi .d {{ font-size: 12.5px; color: {INK_2}; margin-top: 2px; }}
.cr .card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px;
  padding: 18px 20px 12px; margin: 14px 0; }}
.cr .card h3 {{ font-size: 15px; margin: 0; font-weight: 650; color: {INK}; }}
.cr .card p.cap {{ margin: 2px 0 8px; font-size: 13px; color: {INK_3}; }}
.cr svg {{ display: block; width: 100%; height: auto; }}
.cr .legend {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 12.5px; color: {INK_2};
  margin: 2px 0 6px; }}
.cr .legend i {{ display: inline-block; width: 18px; height: 3px; border-radius: 2px;
  vertical-align: middle; margin-right: 6px; }}
.cr .tablewrap {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 16px;
  overflow: hidden; margin: 12px 0; }}
.cr .scroll {{ max-height: 380px; overflow: auto; }}
.cr table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
.cr th {{ position: sticky; top: 0; background: {SURFACE_2}; text-align: right; color: {INK_2};
  font-weight: 600; padding: 10px 14px; border-bottom: 1px solid {LINE_STRONG};
  white-space: nowrap; }}
.cr td {{ text-align: right; padding: 7px 14px; border-bottom: 1px solid {LINE};
  font-family: {FONT_MONO}; font-size: 13px; color: {INK}; }}
.cr th:first-child, .cr td:first-child {{ text-align: left; }}
.cr tr:last-child td {{ border-bottom: 0; }}
.cr tbody tr:nth-child(even) td {{ background: var(--row-alt); }}
.cr .summary {{ background: {SURFACE}; border: 1px solid {LINE}; border-left: 4px solid {BLUE};
  border-radius: 12px; padding: 16px 20px; }}
.cr .summary p {{ margin: 0 0 8px; }}
.cr .summary p:last-child {{ margin: 0; }}
.cr .foot {{ margin-top: 34px; padding-top: 16px; border-top: 1px solid {LINE};
  color: {INK_3}; font-size: 12.5px; }}
.cr .params {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
.cr .params span {{ font-family: {FONT_MONO}; font-size: 12px; background: {SURFACE_2};
  border: 1px solid {LINE}; border-radius: 999px; padding: 3px 10px; color: {INK_2}; }}
@media (max-width: 720px) {{
  .cr {{ padding: 24px 16px 48px; }}
  .cr h1 {{ font-size: 34px; }}
  .cr .kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
}}
</style>"""


def nice_ticks(low: float, high: float, target: int = 5) -> list[float]:
    """Round tick values spanning ``low`` to ``high``."""
    if high <= low:
        high = low + 1.0
    raw = (high - low) / max(1, target)
    magnitude = 10 ** floor(log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = floor(low / step) * step
    ticks = []
    value = start
    while value <= high + step * 0.5 and len(ticks) < 20:
        ticks.append(round(value, 12))
        value += step
    return ticks


def _tick_label(value: float) -> str:
    return sig(value, 3)


def line_chart(
    xs: list[float],
    series: list[dict[str, Any]],
    *,
    x_label: str,
    y_label: str,
    description: str,
    step: bool = False,
    area: bool = False,
    y_zero: bool = False,
    end_labels: bool = False,
) -> str:
    """One responsive SVG line chart. ``series`` items: ys, color, width, opacity, label."""
    left, right = MARGIN["left"], MARGIN["right"] + (60 if end_labels else 0)
    top, bottom = MARGIN["top"], MARGIN["bottom"]
    inner_w, inner_h = WIDTH - left - right, HEIGHT - top - bottom
    values = [y for s in series for y in s["ys"]]
    y_low, y_high = min(values), max(values)
    if y_zero:
        y_low = min(0.0, y_low)
    if y_high == y_low:
        y_high, y_low = y_high + 1.0, y_low - (0.0 if y_zero else 1.0)
    pad = 0.04 * (y_high - y_low)
    ticks_y = nice_ticks(y_low, y_high + pad)
    y_min, y_max = min(ticks_y[0], y_low), max(ticks_y[-1], y_high)
    x_min, x_max = xs[0], xs[-1] if xs[-1] > xs[0] else xs[0] + 1.0
    ticks_x = [t for t in nice_ticks(x_min, x_max, 6) if x_min - 1e-9 <= t <= x_max + 1e-9]

    def px(x: float) -> float:
        return left + (x - x_min) / (x_max - x_min) * inner_w

    def py(y: float) -> float:
        return top + (1 - (y - y_min) / (y_max - y_min)) * inner_h

    out = [
        f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{escape(description)}">',
        f"<title>{escape(description)}</title>",
    ]
    for y in ticks_y:
        if y_min <= y <= y_max:
            out.append(
                f'<line x1="{left}" x2="{WIDTH - right}" y1="{py(y):.1f}" y2="{py(y):.1f}" '
                f'stroke="{LINE}" stroke-width="1"/>'
                f'<text x="{left - 10}" y="{py(y) + 4:.1f}" text-anchor="end" font-size="11" '
                f"font-family='{FONT_MONO}' fill=\"{INK_3}\">{escape(_tick_label(y))}</text>"
            )
    for x in ticks_x:
        out.append(
            f'<line x1="{px(x):.1f}" x2="{px(x):.1f}" y1="{top + inner_h}" '
            f'y2="{top + inner_h + 5}" stroke="{LINE_STRONG}"/>'
            f'<text x="{px(x):.1f}" y="{top + inner_h + 19}" text-anchor="middle" '
            f'font-size="11" font-family=\'{FONT_MONO}\' fill="{INK_3}">'
            f"{escape(_tick_label(x))}</text>"
        )
    out.append(
        f'<line x1="{left}" x2="{WIDTH - right}" y1="{top + inner_h}" y2="{top + inner_h}" '
        f'stroke="{LINE_STRONG}"/>'
    )
    out.append(
        f'<text x="{left + inner_w / 2:.1f}" y="{HEIGHT - 6}" text-anchor="middle" '
        f'font-size="12" font-family=\'{FONT_UI}\' fill="{INK_2}">{escape(x_label)}</text>'
    )
    out.append(
        f'<text transform="translate(14 {top + inner_h / 2:.1f}) rotate(-90)" '
        f'text-anchor="middle" font-size="12" font-family=\'{FONT_UI}\' fill="{INK_2}">'
        f"{escape(y_label)}</text>"
    )
    for s in series:
        points: list[tuple[float, float]] = []
        for i, y in enumerate(s["ys"]):
            if step and i:
                points.append((px(xs[i]), py(s["ys"][i - 1])))
            points.append((px(xs[i]), py(y)))
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        if area:
            baseline = py(max(y_min, 0.0) if y_min <= 0 <= y_max else y_min)
            out.append(
                f'<polygon points="{points[0][0]:.1f},{baseline:.1f} {path} '
                f'{points[-1][0]:.1f},{baseline:.1f}" fill="{s["color"]}" fill-opacity=".08"/>'
            )
        out.append(
            f'<polyline points="{path}" fill="none" stroke="{s["color"]}" '
            f'stroke-width="{s.get("width", 2.2)}" stroke-opacity="{s.get("opacity", 1)}" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        if s.get("dots"):
            for i, y in enumerate(s["ys"]):
                out.append(
                    f'<circle cx="{px(xs[i]):.1f}" cy="{py(y):.1f}" r="2.4" '
                    f'fill="{SURFACE}" stroke="{s["color"]}" stroke-width="1.5"/>'
                )
        if end_labels and s.get("label"):
            out.append(
                f'<text x="{px(xs[-1]) + 8:.1f}" y="{py(s["ys"][-1]) + 4:.1f}" font-size="12" '
                f'font-weight="600" font-family=\'{FONT_UI}\' fill="{s["color"]}">'
                f"{escape(s['label'])}</text>"
            )
    out.append("</svg>")
    return "".join(out)


def header(tables: RunTables) -> str:
    span = f"t = {sig(tables.times[0])} to {sig(tables.times[-1])}"
    return (
        '<div class="cr">'
        '<div class="eyebrow">Consilith &middot; Microstructure</div>'
        "<h1>Grain-growth report</h1>"
        f'<p class="lede">Computed 2D evolution of the reviewed grain map over {escape(span)} '
        f"(dimensionless time), {tables.frames} recorded solver states.</p>"
        f'<div class="note"><b>Hypothetical and dimensionless.</b> {escape(DISCLAIMER)} '
        "Every value below is read from the computed states; none is an experimental result.</div>"
    )


INTRODUCTION = (
    "Normal grain growth is the coarsening of a polycrystal driven by the energy stored in its "
    "grain boundaries. Boundaries move toward their centres of curvature, so small grains with "
    "few neighbours shrink and vanish while the rest grow; the structure stays statistically "
    "self-similar and, in the classical curvature-driven picture, the mean grain area grows "
    "about linearly with time. Abnormal growth, where a few grains outgrow all others, is a "
    "different regime."
)


def _reference(line: str) -> str:
    """One reference line; a trailing http(s) link becomes a link, the rest stays text."""
    parts = line.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].startswith(("https://", "http://")):
        text, url = parts
        return (
            f"<li>{escape(text.rstrip(' -–—'))} "
            f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"{escape(url)}</a></li>"
        )
    return f"<li>{escape(line)}</li>"


def introduction(references: list[str] | None = None) -> str:
    """What normal grain growth is, and the related work the person or their agent chose (for
    example found with Valency). The references are shown as given, never interpreted."""
    refs = [r for r in (references or []) if isinstance(r, str) and r.strip()]
    out = ["<h2>Introduction</h2>", f'<p class="intro">{escape(INTRODUCTION)}</p>']
    if refs:
        out.append('<h3 class="refsTitle">Related work</h3><ol class="refs">')
        out.extend(_reference(r) for r in refs)
        out.append("</ol>")
    return "".join(out)


def _kpi(label: str, value: str, detail: str) -> str:
    return (
        f'<div class="kpi"><div class="k">{escape(label)}</div>'
        f'<div class="v">{escape(value)}</div><div class="d">{escape(detail)}</div></div>'
    )


def _change(first: float, last: float) -> str:
    if first == last:
        return "unchanged"
    if not first:
        return "from zero"
    return f"{'up' if last > first else 'down'} {percent(100 * (last - first) / first)}%"


def kpis(t: RunTables) -> str:
    last = t.frames - 1
    return (
        '<div class="kpis">'
        + _kpi(
            "Visible grains",
            f"{t.counts[0]} → {t.counts[last]}",
            _change(float(t.counts[0]), float(t.counts[last])),
        )
        + _kpi(
            "Free energy",
            f"{sig(t.energy[0])} → {sig(t.energy[last])}",
            _change(t.energy[0], t.energy[last]),
        )
        + _kpi(
            "Mean grain area (px²)",
            f"{sig(t.mean_area(0))} → {sig(t.mean_area(last))}",
            _change(t.mean_area(0), t.mean_area(last)),
        )
        + _kpi(
            "Dimensionless time",
            f"{sig(t.times[0])} → {sig(t.times[last])}",
            f"{t.frames} computed states",
        )
        + "</div>"
    )


def _card(title: str, caption: str, body: str, legend: str = "") -> str:
    return (
        f'<div class="card"><h3>{escape(title)}</h3><p class="cap">{escape(caption)}</p>'
        f"{legend}{body}</div>"
    )


def charts(t: RunTables) -> str:
    energy = line_chart(
        t.times,
        [{"ys": t.energy, "color": BLUE, "width": 2.4, "dots": t.frames <= 31}],
        x_label="Time (dimensionless)",
        y_label="Free energy (dimensionless)",
        description="Free energy against dimensionless time",
        area=True,
    )
    counts = line_chart(
        t.times,
        [{"ys": [float(c) for c in t.counts], "color": TEAL, "width": 2.4}],
        x_label="Time (dimensionless)",
        y_label="Visible grains",
        description="Number of visible grains against dimensionless time",
        step=True,
        y_zero=True,
    )
    mean = line_chart(
        t.times,
        [{"ys": [t.mean_area(i) for i in range(t.frames)], "color": AMBER, "width": 2.4}],
        x_label="Time (dimensionless)",
        y_label="Mean grain area (px squared)",
        description="Mean grain area against dimensionless time",
        y_zero=True,
    )
    fast = t.fastest_shrinking()
    grains = len(t.grain_ids)
    stride = max(1, ceil(grains / MAX_FAINT_GRAINS))
    shown = [g for g in range(grains) if g % stride == 0 and g != fast]
    series: list[dict[str, Any]] = [
        {"ys": [row[g] for row in t.areas], "color": LINE_STRONG, "width": 1.1, "opacity": 0.9}
        for g in shown
    ]
    series.append(
        {
            "ys": [row[fast] for row in t.areas],
            "color": MAGENTA,
            "width": 2.8,
            "label": f"Grain {t.grain_ids[fast]}",
        }
    )
    areas = line_chart(
        t.times,
        series,
        x_label="Time (dimensionless)",
        y_label="Diffuse area (px squared)",
        description="Diffuse area of every grain against dimensionless time",
        y_zero=True,
        end_labels=True,
    )
    drawn = len(shown) + 1
    legend = (
        '<div class="legend">'
        f'<span><i style="background:{MAGENTA}"></i>Grain {t.grain_ids[fast]}, largest share lost'
        f'</span><span><i style="background:{LINE_STRONG}"></i>'
        + (f"{drawn - 1} of {grains - 1} other grains" if stride > 1 else "all other grains")
        + "</span></div>"
    )
    return (
        "<h2>Evolution</h2>"
        + _card(
            "Free energy",
            "Total interface energy of the diffuse-interface fields; it can only fall.",
            energy,
        )
        + _card("Visible grains", "Grains that still own at least one pixel.", counts)
        + _card(
            "Mean grain area",
            "Total diffuse area divided by the visible grains of that state.",
            mean,
        )
        + _card(
            "Grain area history",
            "Diffuse area of each grain. The grain that lost the largest share is highlighted.",
            areas,
            legend,
        )
    )


def table_html(table: dict[str, Any], *, scroll: bool = False) -> str:
    head = "".join(f"<th>{escape(c)}</th>" for c in table["columns"])
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in table["rows"]
    )
    inner = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return f'<div class="tablewrap"><div class="{"scroll" if scroll else ""}">{inner}</div></div>'


def tables_section(t: RunTables) -> str:
    return (
        "<h2>Tables</h2>"
        '<p class="cap" style="color:var(--ink-3);margin:0 0 4px">Every recorded state of the run.</p>'
        + table_html(t.history_table(), scroll=True)
        + '<p class="cap" style="color:var(--ink-3);margin:18px 0 4px">'
        "The grains that lost the largest share of their first-state area.</p>"
        + table_html(t.shrinking_table())
    )


def summary_text(t: RunTables) -> list[str]:
    """Plain sentences built from the tables; the same numbers the charts and tables show."""
    last = t.frames - 1
    fast = t.fastest_shrinking()
    gone = t.vanished()
    lines = [
        f"Between t={sig(t.times[0])} and t={sig(t.times[last])} (dimensionless time) the number "
        f"of visible grains went from {t.counts[0]} to {t.counts[last]} and the free energy "
        f"went from {sig(t.energy[0])} to {sig(t.energy[last])}.",
        f"The mean grain area went from {sig(t.mean_area(0))} to {sig(t.mean_area(last))} "
        "px squared.",
        f"Grain {t.grain_ids[fast]} shrank fastest, losing "
        f"{percent(100 * t.fractional_loss(fast))}% of its first-state diffuse area.",
    ]
    if gone:
        shown = ", ".join(str(g) for g in gone[:12]) + (", ..." if len(gone) > 12 else "")
        lines.append(f"{len(gone)} grain(s) had no diffuse area left in the last state: {shown}.")
    return lines


def summary_section(t: RunTables) -> str:
    paragraphs = "".join(f"<p>{escape(line)}</p>" for line in summary_text(t))
    return f"<h2>Summary</h2><div class='summary'>{paragraphs}</div>"


def footer(t: RunTables) -> str:
    labels = {
        "duration": "duration",
        "gamma": "interface energy",
        "mobility": "mobility",
        "width": "interface width (px)",
    }
    chips = "".join(
        f"<span>{escape(labels[k])} = {escape(sig(v))}</span>" for k, v in t.parameters.items()
    )
    params = f'<div class="params">{chips}</div>' if chips else ""
    return (
        '<div class="foot">Source: computed solver states of the reviewed grain map; no '
        "interpolated frames. This report presents the run's tables. It does not check the "
        "physics, and it makes no claim about any material."
        f"{params}</div></div>"
    )
