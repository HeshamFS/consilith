import { useId, useMemo, useState } from "react";
import { Icon, type StampKind } from "@/shared/ui";
import b from "./blocks.module.css";
import { fmt, sourceLine, unitLabel } from "./format";
import type { DataTableData, TableColumn, TableRow } from "./types";

/** A table of stored rows. The server gives the columns (with units) and at most 200 rows, each
 * under its own provenance id, stamp and status; this component only lays them out and sorts
 * them on screen. Sorting changes the order of what is shown and nothing else. */

export const MAX_ROWS = 200;

type Sort = { key: string; dir: "asc" | "desc" };
type Cell = { text: string; number: number | null; unit: string; muted: boolean };

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** A stored cell as text, with its number kept for sorting. */
export function cellOf(raw: unknown): Cell {
  if (typeof raw === "number" && Number.isFinite(raw))
    return { text: fmt(raw), number: raw, unit: "", muted: false };
  if (typeof raw === "boolean")
    return { text: raw ? "Yes" : "No", number: null, unit: "", muted: false };
  if (typeof raw === "string")
    return raw === ""
      ? { text: "—", number: null, unit: "", muted: true }
      : { text: raw, number: null, unit: "", muted: false };
  if (isRecord(raw)) {
    if (typeof raw.v === "number" && Number.isFinite(raw.v))
      return {
        text: fmt(raw.v),
        number: raw.v,
        unit: typeof raw.unit === "string" ? raw.unit : "",
        muted: false,
      };
    if (typeof raw.placeholder === "string")
      return { text: raw.placeholder, number: null, unit: "", muted: true };
    if (typeof raw.text === "string")
      return { text: raw.text, number: null, unit: "", muted: false };
  }
  return { text: "—", number: null, unit: "", muted: true };
}

/** Rows in the order asked for. Numbers sort as numbers, text by locale with digits in order,
 * and a missing cell goes last either way. The sort is stable. */
export function sortRows(rows: readonly TableRow[], sort: Sort | null): TableRow[] {
  const keyed = rows.map((row, at) => ({ row, at }));
  if (!sort) return keyed.map((k) => k.row);
  const sign = sort.dir === "asc" ? 1 : -1;
  keyed.sort((p, q) => {
    const a = cellOf(p.row.cells[sort.key]);
    const c = cellOf(q.row.cells[sort.key]);
    if (a.muted !== c.muted) return a.muted ? 1 : -1;
    let diff = 0;
    if (a.number !== null && c.number !== null) diff = a.number - c.number;
    else diff = a.text.localeCompare(c.text, undefined, { numeric: true, sensitivity: "base" });
    return diff === 0 ? p.at - q.at : diff * sign;
  });
  return keyed.map((k) => k.row);
}

function numericColumn(col: TableColumn, rows: readonly TableRow[]): boolean {
  if (col.kind) return col.kind === "number";
  return rows.length > 0 && rows.every((r) => cellOf(r.cells[col.key]).number !== null);
}

export function DataTableView({
  data,
  title,
  variant,
}: {
  data: DataTableData;
  title?: string | undefined;
  variant?: string | undefined;
}) {
  const uid = useId();
  const [sort, setSort] = useState<Sort | null>(null);
  const cols = useMemo(() => data.columns.filter((c) => c && typeof c.key === "string"), [data]);
  const shown = useMemo(() => data.rows.slice(0, MAX_ROWS), [data]);
  const order = useMemo(() => sortRows(shown, sort), [shown, sort]);
  const numeric = useMemo(() => cols.map((c) => numericColumn(c, shown)), [cols, shown]);
  const heading = title ?? data.title ?? "Table";
  const total = data.total ?? shown.length;
  const cut = data.truncated === true || data.rows.length > MAX_ROWS || total > shown.length;
  const first = cols[0];
  if (!first) return <p className={b.empty}>No table data yet</p>;
  const next = (key: string) =>
    setSort((cur) =>
      cur?.key !== key ? { key, dir: "asc" } : cur.dir === "asc" ? { key, dir: "desc" } : null,
    );
  const sortedLabel = sort
    ? `Sorted by ${cols.find((c) => c.key === sort.key)?.label ?? sort.key}, ${
        sort.dir === "asc" ? "ascending" : "descending"
      }`
    : "";
  return (
    <section
      className={b.table}
      data-variant={variant ?? "table"}
      aria-label={heading}
      aria-describedby={`${uid}-n`}
    >
      <header className={b.tableHead}>
        <h3 className={b.tableTitle}>{heading}</h3>
        <span className={b.count} id={`${uid}-n`}>
          {shown.length === 0
            ? "No rows"
            : cut
              ? `${shown.length} of ${total} rows`
              : `${shown.length} ${shown.length === 1 ? "row" : "rows"}`}
        </span>
      </header>
      {shown.length === 0 ? (
        <p className={b.empty}>No stored rows yet</p>
      ) : (
        <section
          className={b.scroll}
          aria-label={`${heading}, scrollable`}
          // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table is scrolled with the keyboard
          tabIndex={0}
        >
          <table>
            <thead>
              <tr>
                {cols.map((c, i) => (
                  <th
                    key={c.key}
                    scope="col"
                    data-num={numeric[i]}
                    data-first={i === 0}
                    aria-sort={
                      sort?.key === c.key
                        ? sort.dir === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      className={b.sortBtn}
                      data-dir={sort?.key === c.key ? sort.dir : "none"}
                      onClick={() => next(c.key)}
                    >
                      <span className={b.colLabel}>{c.label}</span>
                      {c.unit ? <span className={b.colUnit}>{unitLabel(c.unit)}</span> : null}
                      <i className={b.caret} aria-hidden="true" />
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {order.map((row) => (
                <tr
                  key={shown.indexOf(row)}
                  data-stamp={row.stamp ?? "none"}
                  data-off={!!row.status && row.status !== "current"}
                  title={row.provenanceId ? `provenance ${row.provenanceId}` : undefined}
                >
                  {cols.map((c, i) => {
                    const cell = cellOf(row.cells[c.key]);
                    const showUnit = cell.unit && cell.unit !== c.unit;
                    if (c === first)
                      return (
                        <th key={c.key} scope="row" data-first="true">
                          <span className={b.rowHead}>
                            <StampGlyph kind={row.stamp} />
                            <span className={b.rowText}>
                              <span data-muted={cell.muted}>{cell.text}</span>
                              {row.status && row.status !== "current" ? (
                                <span className={b.rowStatus}>{row.status}</span>
                              ) : null}
                            </span>
                          </span>
                        </th>
                      );
                    return (
                      <td key={c.key} data-num={numeric[i]} data-muted={cell.muted}>
                        {cell.text}
                        {showUnit ? (
                          <span className={b.cellUnit}>{unitLabel(cell.unit)}</span>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <p className="visually-hidden" role="status" aria-live="polite">
        {sortedLabel}
      </p>
      <footer className={b.tableFoot}>
        {cut ? <span>Showing the first {shown.length} stored rows.</span> : null}
        {sourceLine(data) ? <span>{sourceLine(data)}</span> : null}
      </footer>
    </section>
  );
}

const GLYPH: Record<StampKind, string> = {
  verified: "Verified",
  checking: "Checking",
  warning: "Warning",
  blocked: "Blocked",
};

function StampGlyph({ kind }: { kind: TableRow["stamp"] }) {
  if (!kind) return <i className={b.glyphNone} aria-hidden="true" />;
  return (
    <span
      className={b.glyph}
      data-stamp={kind}
      role="img"
      aria-label={GLYPH[kind]}
      title={GLYPH[kind]}
    >
      <Icon name={kind} size={14} />
    </span>
  );
}
