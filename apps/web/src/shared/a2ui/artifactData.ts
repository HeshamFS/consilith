/** The JSON a plugin stores for a chart or a table, read into the components that draw them.
 *
 * One contract, documented in docs/plugin-authoring.md:
 *   {"kind": "chart", "chart": <ChartData>}                      line, scatter, bar, histogram
 *   {"kind": "table", "columns": [...], "rows": [...], "units"?, "labels"?, "caption"?}
 * and two older shapes that keep working: {columns, rows} without a kind is a table, and {x, y}
 * is one line. Nothing here computes a value. A long series is thinned by picking points; the
 * rest is reading and naming. */

import { selectIndices } from "./chartMath";
import type { ChartData, ChartSeries, DataTableData, TableColumn, TableRow } from "./types";

export const MAX_SERIES = 12;
export const MAX_POINTS = 2000;
export const MAX_TABLE_ROWS = 5000;
export const MAX_TABLE_COLUMNS = 400;

export type PlotArtifact =
  | { kind: "chart"; chart: ChartData }
  | { kind: "table"; table: DataTableData; caption?: string };

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

const KINDS = ["line", "scatter", "bar"] as const;
const NUMERIC = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

function readSeries(raw: unknown, bars: boolean): ChartSeries | null {
  if (!isRecord(raw) || !Array.isArray(raw.x) || !Array.isArray(raw.y)) return null;
  if (raw.x.length !== raw.y.length || raw.x.length === 0) return null;
  if (!raw.y.every(isNum)) return null;
  if (!raw.x.every((v) => (bars ? typeof v === "string" || isNum(v) : isNum(v)))) return null;
  const x = raw.x as (number | string)[];
  const y = raw.y as number[];
  const keep = bars ? null : selectIndices(x.length, MAX_POINTS);
  const series: ChartSeries = {
    name: str(raw.name) || "Series",
    x: keep ? keep.map((i) => x[i] ?? 0) : x,
    y: keep ? keep.map((i) => y[i] ?? 0) : y,
  };
  if (typeof raw.unit === "string") series.unit = raw.unit;
  if (keep && keep.length < x.length) series.decimatedFrom = x.length;
  else if (isNum(raw.decimatedFrom)) series.decimatedFrom = raw.decimatedFrom;
  return series;
}

/** A chart from `{kind: "chart", chart}`; null when it is not a drawable chart. */
function readChart(raw: unknown): ChartData | null {
  if (!isRecord(raw)) return null;
  const kind = KINDS.find((k) => k === raw.kind) ?? "line";
  const bars = kind === "bar";
  if (!Array.isArray(raw.series)) return null;
  const series = raw.series
    .slice(0, MAX_SERIES)
    .map((s) => readSeries(s, bars))
    .filter((s): s is ChartSeries => s !== null);
  if (series.length === 0) return null;
  const chart: ChartData = {
    kind,
    xLabel: str(raw.xLabel) || "x",
    yLabel: str(raw.yLabel) || "y",
    xUnit: str(raw.xUnit),
    yUnit: str(raw.yUnit),
    series,
  };
  if (typeof raw.title === "string") chart.title = raw.title;
  if (isNum(raw.omittedSeries)) chart.omittedSeries = raw.omittedSeries;
  else if (raw.series.length > MAX_SERIES) chart.omittedSeries = raw.series.length - MAX_SERIES;
  return chart;
}

/** A trailing "(unit)" in a column name, split off: "Time (dimensionless)" is Time in dimensionless. */
function splitUnit(name: string): { label: string; unit: string } {
  const m = /^(.*?)\s*\(([^()]+)\)\s*$/.exec(name);
  return m?.[1] && m[2] ? { label: m[1], unit: m[2] } : { label: name, unit: "" };
}

function prettify(name: string): string {
  const spaced = name.replaceAll("_", " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : name;
}

function cell(v: unknown): unknown {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") {
    // Only when the number reads back as the same text: "007" and "1e5" stay as written.
    const s = v.trim();
    return NUMERIC.test(s) && String(Number(s)) === s ? Number(s) : v;
  }
  return v;
}

function readTable(raw: Record<string, unknown>): DataTableData | null {
  if (!Array.isArray(raw.columns) || !Array.isArray(raw.rows)) return null;
  if (raw.columns.length === 0 || raw.columns.length > MAX_TABLE_COLUMNS) return null;
  const units = isRecord(raw.units) ? raw.units : {};
  const labels = isRecord(raw.labels) ? raw.labels : {};
  const keys: string[] = [];
  const columns: TableColumn[] = [];
  for (const c of raw.columns) {
    const base = isRecord(c) ? str(c.key) : str(c);
    if (!base) return null;
    let key = base;
    for (let n = 2; keys.includes(key); n++) key = `${base}#${n}`;
    keys.push(key);
    const named = isRecord(c) ? str(c.label) : "";
    const unit = (isRecord(c) ? str(c.unit) : "") || str(units[base]);
    const label = named || str(labels[base]);
    if (label || unit)
      columns.push({ key, label: label || prettify(base), ...(unit ? { unit } : {}) });
    else {
      const split = splitUnit(prettify(base));
      columns.push({ key, label: split.label, ...(split.unit ? { unit: split.unit } : {}) });
    }
  }
  const rows: TableRow[] = [];
  for (const r of raw.rows.slice(0, MAX_TABLE_ROWS)) {
    const cells: Record<string, unknown> = {};
    if (Array.isArray(r)) {
      keys.forEach((key, i) => {
        cells[key] = cell(r[i]);
      });
    } else if (isRecord(r)) {
      raw.columns.forEach((c, i) => {
        const source = isRecord(c) ? str(c.key) : str(c);
        cells[keys[i] ?? source] = cell(r[source]);
      });
    } else return null;
    rows.push({ cells });
  }
  for (const col of columns) {
    const filled = rows.map((r) => r.cells[col.key]).filter((v) => v !== null && v !== "");
    col.kind = filled.length > 0 && filled.every(isNum) ? "number" : "text";
  }
  const table: DataTableData = { columns, rows };
  if (typeof raw.title === "string") table.title = raw.title;
  if (raw.rows.length > MAX_TABLE_ROWS) {
    table.truncated = true;
    table.total = raw.rows.length;
  }
  return table;
}

/** Read stored JSON as a chart or a table, or null when it is neither (it stays a raw file). */
export function parsePlotArtifact(data: unknown): PlotArtifact | null {
  if (!isRecord(data)) return null;
  if (data.kind === "chart") {
    const chart = readChart(data.chart);
    return chart ? { kind: "chart", chart } : null;
  }
  if (data.kind === "table" || (data.kind === undefined && "columns" in data && "rows" in data)) {
    const table = readTable(data);
    if (!table) return null;
    return {
      kind: "table",
      table,
      ...(typeof data.caption === "string" && data.caption ? { caption: data.caption } : {}),
    };
  }
  if (
    data.kind === undefined &&
    Array.isArray(data.x) &&
    Array.isArray(data.y) &&
    data.x.length === data.y.length &&
    data.x.length > 1 &&
    data.x.length <= 20000 &&
    [...data.x, ...data.y].every(isNum)
  ) {
    const chart = readChart({
      kind: "line",
      title: data.title,
      xLabel: data.xLabel,
      yLabel: data.yLabel,
      xUnit: data.xUnit,
      yUnit: data.yUnit,
      series: [{ name: str(data.name) || "Stored series", x: data.x, y: data.y }],
    });
    return chart ? { kind: "chart", chart } : null;
  }
  return null;
}

function csvCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  // A plugin string that starts like a formula opens as text in a spreadsheet, never runs.
  const raw = String(v);
  const text = typeof v === "string" && /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

/** The table as CSV, one header line with units in parentheses, then every stored row. */
export function tableToCsv(table: DataTableData): string {
  const head = table.columns.map((c) => csvCell(c.unit ? `${c.label} (${c.unit})` : c.label));
  const lines = [head.join(",")];
  for (const row of table.rows)
    lines.push(table.columns.map((c) => csvCell(row.cells[c.key])).join(","));
  return `${lines.join("\n")}\n`;
}

/** A JSON-lines file as a table of its records (columns from the first record), or its text
 * when a line is not a JSON object. */
export function jsonLines(text: string): unknown {
  const records: Record<string, unknown>[] = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const value: unknown = JSON.parse(line);
      if (!value || typeof value !== "object" || Array.isArray(value)) return text;
      records.push(value as Record<string, unknown>);
    } catch {
      return text;
    }
  }
  const columns = Object.keys(records[0] ?? {});
  if (!columns.length) return text;
  return {
    kind: "table",
    columns,
    rows: records,
    caption: `${records.length} records`,
  };
}
