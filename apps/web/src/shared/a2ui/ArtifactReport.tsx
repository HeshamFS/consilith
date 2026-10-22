import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { AppFrame } from "./AppFrame";
import s from "./artifact-data.module.css";
import { jsonLines, type PlotArtifact, parsePlotArtifact } from "./artifactData";
import { ChartView } from "./Chart";
import { PluginTable } from "./DataArtifact";

/** A result's artifacts laid out like a report: apps and reports first, then charts in a
 * responsive grid, then tables, then the raw files. Which is which is read from the stored
 * JSON itself (`{kind: "chart"}` and `{kind: "table"}`, see artifactData.ts) and from the
 * artifact's role (`app`, `report`), so a plugin ships no layout of its own. */

export type ReportItem = {
  label?: string;
  sha256?: string;
  source?: string;
  role?: string;
  kind?: string;
  data?: unknown;
  plugin?: string;
  illustrative?: boolean;
  provenanceId?: string;
  /** Reserved for a plugin that names the artifact its app draws (`meta.data`). */
  meta?: { data?: string };
};

export type ItemClass = "app" | "chart" | "table" | "file";
export type Classified = { item: ReportItem; kind: ItemClass; plot?: PlotArtifact; json?: unknown };

const MAX_CLASSIFIED = 40;
const MAX_JSON_BYTES = 4 * 1024 * 1024;
export const APP_ROLES = ["app", "report"];

export const isAppRole = (role: string | undefined) => !!role && APP_ROLES.includes(role);

/** The artifact name with the underscores a plugin uses for words taken out. */
export const prettyLabel = (label: string | undefined) =>
  (label ?? "Artifact").replaceAll("_", " ");

type Load = (sha: string) => Promise<Blob>;

/** Loads each artifact once for the whole report; the file tabs reuse the same bytes. */
function useSharedLoad(load: Load | undefined): Load | undefined {
  return useMemo(() => {
    if (!load) return undefined;
    const cache = new Map<string, Promise<Blob>>();
    return (sha: string) => {
      let hit = cache.get(sha);
      if (!hit) {
        hit = load(sha);
        hit.catch(() => cache.delete(sha));
        cache.set(sha, hit);
      }
      return hit;
    };
  }, [load]);
}

async function classify(item: ReportItem, load: Load | undefined): Promise<Classified> {
  if (isAppRole(item.role)) return { item, kind: "app" };
  const base = (json?: unknown): Classified => ({
    item,
    kind: "file",
    ...(json !== undefined ? { json } : {}),
  });
  if (item.source === "input") return base();
  let json: unknown = item.data;
  if (json === undefined && item.sha256 && load) {
    try {
      const blob = await load(item.sha256);
      if (/jsonl|ndjson/.test(blob.type) && blob.size <= MAX_JSON_BYTES) {
        const rows = jsonLines(await blob.text());
        if (typeof rows !== "string") json = rows;
      } else if (blob.type.includes("json") && blob.size <= MAX_JSON_BYTES)
        json = JSON.parse(await blob.text());
    } catch {
      return base();
    }
  }
  const plot = json === undefined ? null : parsePlotArtifact(json);
  return plot ? { item, kind: plot.kind, plot, json } : base(json);
}

/** The class of every artifact, read from its bytes. `ready` is false until all are read. */
export function useClassified(items: ReportItem[], load: Load | undefined) {
  const shared = useSharedLoad(load);
  // The same artifacts under a new array (a parent rendering again) are not a new question.
  const key = items.map((i) => `${i.sha256 ?? ""}|${i.role ?? ""}|${i.label ?? ""}`).join(",");
  const latest = useRef(items);
  latest.current = items;
  const [done, setDone] = useState<{ key: string; list: Classified[] } | null>(null);
  useEffect(() => {
    let live = true;
    const taken = latest.current.slice(0, MAX_CLASSIFIED);
    // Past the cap an artifact is not read, but it stays a plain file in the Files list.
    const rest: Classified[] = latest.current
      .slice(MAX_CLASSIFIED)
      .map((item) => ({ item, kind: "file" }));
    void Promise.all(taken.map((item) => classify(item, shared))).then((list) => {
      if (live) setDone({ key, list: [...list, ...rest] });
    });
    return () => {
      live = false;
    };
  }, [key, shared]);
  const ready = done !== null && done.key === key;
  return { ready, list: ready ? done.list : [], load: shared };
}

/** A report layout is worth it once one artifact is an app, a chart or a table. */
export const isReport = (list: Classified[]) => list.some((c) => c.kind !== "file");

/** The artifact an app draws: the one it names in `meta.data`, else `<stem>_json` for a
 * `<stem>_viewer` or `<stem>_report`. */
export function dataFor(app: ReportItem, list: Classified[]): Classified | undefined {
  const label = app.label ?? "";
  const stem = label.replace(/_(viewer|report|app)$/, "");
  const wanted = app.meta?.data ?? `${stem}_json`;
  return list.find((c) => c.item.label === wanted && c.json !== undefined);
}

/** The table shown behind an app's "Data table" toggle: `<stem>_table`, else the first table. */
export function tableFor(app: ReportItem, list: Classified[]): Classified | undefined {
  const stem = (app.label ?? "").replace(/_(viewer|report|app)$/, "");
  const tables = list.filter((c) => c.kind === "table");
  return tables.find((c) => c.item.label === `${stem}_table`) ?? tables[0];
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: ReactNode;
}) {
  return (
    <section className={s.section} aria-label={title}>
      <h4 className={s.sectionTitle}>
        {title} <span className={s.count}>{count}</span>
      </h4>
      {children}
    </section>
  );
}

export function ArtifactReport({
  list,
  appLink,
  files,
}: {
  list: Classified[];
  appLink?: ((sha: string) => Promise<string>) | undefined;
  /** The raw files, in the tabbed browser. */
  files: ReactNode;
}) {
  const apps = list.filter((c) => c.kind === "app");
  const charts = list.filter((c) => c.kind === "chart");
  const tables = list.filter((c) => c.kind === "table");
  const hasFiles = list.some((c) => c.kind === "file");
  return (
    <div className={s.report}>
      {apps.length ? (
        <Section title="Apps and reports" count={apps.length}>
          <div className={s.stack}>
            {apps.map((c) => {
              const data = dataFor(c.item, list);
              const table = tableFor(c.item, list);
              return (
                <AppFrame
                  key={c.item.sha256 ?? c.item.label}
                  sha={c.item.sha256 ?? ""}
                  title={prettyLabel(c.item.label)}
                  initialHeight={c.item.role === "report" ? 640 : 420}
                  {...(c.item.plugin ? { plugin: c.item.plugin } : {})}
                  {...(data ? { data: data.json } : {})}
                  {...(appLink ? { link: appLink } : {})}
                  fallback={
                    table?.plot?.kind === "table" ? (
                      <PluginTable
                        table={table.plot.table}
                        caption={table.plot.caption}
                        title={prettyLabel(table.item.label)}
                      />
                    ) : undefined
                  }
                />
              );
            })}
          </div>
        </Section>
      ) : null}
      {charts.length ? (
        <Section title="Charts" count={charts.length}>
          <div className={s.grid}>
            {charts.map((c) =>
              c.plot?.kind === "chart" ? (
                <ChartView
                  key={c.item.sha256 ?? c.item.label}
                  data={c.plot.chart}
                  title={c.plot.chart.title ?? prettyLabel(c.item.label)}
                />
              ) : null,
            )}
          </div>
        </Section>
      ) : null}
      {tables.length ? (
        <Section title="Tables" count={tables.length}>
          <div className={s.stack}>
            {tables.map((c) =>
              c.plot?.kind === "table" ? (
                <PluginTable
                  key={c.item.sha256 ?? c.item.label}
                  table={c.plot.table}
                  caption={c.plot.caption}
                  title={c.plot.table.title ?? prettyLabel(c.item.label)}
                />
              ) : null,
            )}
          </div>
        </Section>
      ) : null}
      {hasFiles ? (
        <Section title="Files" count={list.filter((c) => c.kind === "file").length}>
          {files}
        </Section>
      ) : null}
    </div>
  );
}
