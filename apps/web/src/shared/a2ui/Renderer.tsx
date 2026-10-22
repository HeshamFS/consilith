import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Icon, Stamp, type StampKind } from "@/shared/ui";
import {
  AssemblyBar,
  AssemblyProvider,
  accentStyle,
  Reveal,
  unitsOf,
  useAssembly,
} from "./Assembly";
import s from "./a2ui.module.css";
import { ChartView } from "./Chart";
import { DataTableView } from "./DataTableView";
import { fmt, unitLabel } from "./format";
import { HeatmapView } from "./Heatmap";
import type { ImageActivity } from "./ImageStage";
import { Provenance } from "./Provenance";
import { frameRoot, weightOf } from "./planGeometry";
import { ResultValue, type TypedResult } from "./ResultValue";
import { RichTextView } from "./RichTextView";
import { resolve } from "./surface";
import {
  type ChartData,
  type Component,
  DATA_VIEWS,
  type DataTableData,
  type HeatmapData,
  PANELS,
  type Quantity,
  type RichTextData,
  type RunLive,
  type Surface,
  type TextValue,
} from "./types";
import { Layout } from "./WorkspaceLayout";
import { type WorkspaceActions, WorkspaceView } from "./WorkspaceViews";

/** Renders one A2UI surface from the room's closed catalog. It never computes a number: every
 * value shown comes from the data model with its provenance id, and an illustrative value says
 * so. Unknown components render nothing. Actions go to `onAction`; the room has no A2UI action
 * endpoint, so the page turns them into REST calls. */

export type A2UIAction =
  | {
      name: "decide";
      proposalId: string;
      accept: boolean;
      reason: string;
    }
  | { name: "run"; capability: string }
  | { name: "edit"; path: string; value: unknown; present: boolean; reason: string };

type Ctx = {
  imageActivity: ImageActivity;
  surface: Surface;
  scope?: string;
  onAction?: (a: A2UIAction) => Promise<void>;
  views?: Partial<Record<string, (data: unknown, c: Component) => ReactNode>>;
  /** The surface fills a fixed frame: every cell gets a share of it (see planGeometry.ts). */
  bounded?: boolean;
  loadArtifact?: WorkspaceActions["loadArtifact"];
  uploadArtifact?: WorkspaceActions["uploadArtifact"];
  live?: Record<string, RunLive>;
};

const PANEL_SET = new Set<string>(PANELS);
const VIEW_SET = new Set<string>(DATA_VIEWS);

export function A2UISurface({
  surface,
  scope,
  onAction,
  views,
  loadArtifact,
  uploadArtifact,
  live,
  assemble = false,
  accent,
  agent = "Your agent",
  bounded = false,
  quiet = false,
  startDelayMs,
  budgetMs,
  onAssembled,
}: {
  surface: Surface;
  scope?: string;
  onAction?: Ctx["onAction"];
  views?: Ctx["views"];
  loadArtifact?: WorkspaceActions["loadArtifact"];
  /** Stores a file picked in an `artifact` field and returns its sha256. */
  uploadArtifact?: WorkspaceActions["uploadArtifact"];
  /** Progress and queue state of runs by run id, for a JobStatus to join. */
  live?: Record<string, RunLive>;
  /** Stage the surface panel by panel (skeletons, then each panel in reading order). */
  assemble?: boolean;
  /** The requesting seat's colour (a CSS colour or var()) for the ring and caption. */
  accent?: string;
  /** Who is placing the panels, for the caption. */
  agent?: string;
  /** Fill the frame that holds the surface: panels in a row share its height and scroll inside
   * their own box, so nothing is taller or wider than its cell. */
  bounded?: boolean;
  /** Inside the assembly frame: an unplaced panel stays empty (the frame's boxes stand there). */
  quiet?: boolean;
  /** Hold the first panel this long (the frame's boxes are still landing). */
  startDelayMs?: number;
  /** The whole staging stays within this many ms. */
  budgetMs?: number;
  /** Called once every panel is placed, or at once when nothing is staged. */
  onAssembled?: () => void;
}) {
  const units = useMemo(() => unitsOf(surface), [surface]);
  const staging = useAssembly(units, assemble, agent, {
    ...(startDelayMs !== undefined ? { delayMs: startDelayMs } : {}),
    ...(budgetMs !== undefined ? { budgetMs } : {}),
    quiet,
  });
  const settled = !staging || staging.phase === "rest";
  const done = useRef(onAssembled);
  done.current = onAssembled;
  useEffect(() => {
    if (settled) done.current?.();
  }, [settled]);
  const ctx: Ctx = {
    bounded,
    surface,
    imageActivity: surfaceImageActivity(surface),
    ...(scope ? { scope } : {}),
    loadArtifact,
    ...(uploadArtifact ? { uploadArtifact } : {}),
    ...(live ? { live } : {}),
    ...(onAction ? { onAction } : {}),
    ...(views ? { views } : {}),
  };
  return (
    <AssemblyProvider value={staging}>
      <div
        className={s.surface}
        data-surface={surface.id}
        data-assembly={staging ? staging.phase : undefined}
        data-bounded={bounded ? "true" : undefined}
        aria-busy={staging && staging.phase !== "rest" ? true : undefined}
        style={staging ? accentStyle(accent) : undefined}
      >
        {bounded ? null : <AssemblyBar />}
        <Node id="root" ctx={ctx} depth={0} fill={false} />
      </div>
    </AssemblyProvider>
  );
}

/** Use the surface's own execution binding, independent of its plugin or layout. */
function surfaceImageActivity(surface: Surface): ImageActivity {
  const status = Object.values(surface.components).find(
    (component) => component.component === "JobStatus",
  );
  if (!status || !("data" in status)) return "idle";
  const value = resolve(surface.data, status.data.path);
  if (!value || typeof value !== "object" || !("state" in value)) return "idle";
  const state = value.state;
  return state === "running" || state === "queued" || state === "done" || state === "failed"
    ? state
    : "idle";
}

function text(v: TextValue | undefined, data: unknown): string {
  if (v === undefined) return "";
  if (typeof v === "string") return v;
  const r = resolve(data, v.path);
  if (typeof r === "string" || typeof r === "number") return String(r);
  if (r && typeof r === "object" && "text" in r) return String((r as { text: unknown }).text);
  return "";
}

function isQuantity(v: unknown): v is Quantity {
  return (
    !!v &&
    typeof v === "object" &&
    typeof (v as Quantity).v === "number" &&
    typeof (v as Quantity).unit === "string" &&
    typeof (v as Quantity).provenanceId === "string"
  );
}

export function QuantityView({ q }: { q: Quantity }) {
  return (
    <span
      className={s.qty}
      title={`provenance ${q.provenanceId}${q.tier !== undefined ? ` · tier ${q.tier}` : ""}`}
    >
      <span className={s.num}>{fmt(q.v)}</span>
      {q.unit ? <span className={s.unit}>{unitLabel(q.unit)}</span> : null}
      {q.stamp ? <Stamp kind={q.stamp as StampKind} /> : null}
      {q.status && q.status !== "current" ? <span className={s.status}>{q.status}</span> : null}
      {q.illustrative ? <span className={s.ill}>illustrative</span> : null}
    </span>
  );
}

function Value({ v }: { v: unknown }) {
  if (
    v &&
    typeof v === "object" &&
    "kind" in v &&
    ["text", "category", "json", "artifact"].includes(String(v.kind))
  )
    return <ResultValue result={v as TypedResult} />;
  if (isQuantity(v)) return <QuantityView q={v} />;
  if (v && typeof v === "object" && "placeholder" in v)
    return (
      <span className={s.placeholder}>{String((v as { placeholder: unknown }).placeholder)}</span>
    );
  if (v && typeof v === "object" && "text" in v)
    return <span>{String((v as { text: unknown }).text)}</span>;
  if (typeof v === "string") return <span>{v}</span>;
  return <span className={s.placeholder}>not set</span>;
}

/** One journal entry as a person reads it; the raw kind and seq stay in the tooltip. */
const LOG_PHRASES: Record<string, string> = {
  "room.created": "opened",
  "room.join": "joined",
  "room.leave": "left",
  "room.case": "started a case",
  "lane.claim": "claimed a lane",
  "lane.claim.all": "took every lane",
  "lane.release": "released a lane",
  "lane.handover": "handed over a lane",
  "agent.brought": "brought their agent",
  "agent.config": "changed their agent",
  "agent.status": "agent status changed",
  "plugin.activated": "activated a plugin",
  "plugin.migrated": "updated the plugins",
  "twin.edit": "edited an input",
};
export function logLine(actor: string, kind: string): string {
  const who = actor === "backend" ? "Room" : actor.charAt(0).toUpperCase() + actor.slice(1);
  return `${who} ${LOG_PHRASES[kind] ?? kind.replace(/[._]/g, " ")}`;
}

/** A panel is staged by Reveal (a skeleton, then the panel); anything else renders directly. */
function Node({
  id,
  ctx,
  depth,
  fill,
  weight,
}: {
  id: string;
  ctx: Ctx;
  depth: number;
  /** Inside the workspace of a bounded surface: the node sits in a cell of the frame. */
  fill: boolean;
  /** Share of the parent's height; 1 where the parent divides width or both ways. */
  weight?: number;
}) {
  const inner = (
    <Reveal id={id}>
      <NodeBody id={id} ctx={ctx} depth={depth} fill={fill} />
    </Reveal>
  );
  if (!ctx.bounded || !fill) return inner;
  const c = ctx.surface.components[id];
  const leaf =
    !!c &&
    c.component !== "Column" &&
    c.component !== "Row" &&
    c.component !== "Card" &&
    c.component !== "WorkspaceLayout";
  return (
    <div
      className={s.cell}
      data-cell={id}
      data-leaf={leaf ? "true" : undefined}
      style={{ ["--w" as string]: weight ?? 1 } as CSSProperties}
    >
      {inner}
    </div>
  );
}

function NodeBody({
  id,
  ctx,
  depth,
  fill,
}: {
  id: string;
  ctx: Ctx;
  depth: number;
  fill: boolean;
}) {
  const c = ctx.surface.components[id];
  if (!c || depth > 24) return null;
  const data = ctx.surface.data;
  const rootKids = id === "root" && ctx.bounded;
  const frame = rootKids ? frameRoot(ctx.surface) : null;
  const kids = (ids: string[], equal = false, within = fill) =>
    ids.map((k) => (
      <Node
        key={k}
        id={k}
        ctx={ctx}
        depth={depth + 1}
        fill={rootKids ? k === frame : within}
        weight={equal ? 1 : weightOf(ctx.surface, k)}
      />
    ));
  switch (c.component) {
    case "WorkspaceLayout":
      return (
        <Layout component={c}>
          {kids(c.children, c.kind !== "stack" && c.kind !== "section")}
        </Layout>
      );
    case "Column":
      return <div className={s.column}>{kids(c.children)}</div>;
    case "Row":
      return <div className={s.row}>{kids(c.children, true)}</div>;
    case "Card":
      return <div className={s.card}>{kids([c.child])}</div>;
    case "Divider":
      return <hr className={s.divider} />;
    case "Text": {
      const t = text(c.text, data);
      if (c.variant === "h1") return <h2 className={s.h1}>{t}</h2>;
      if (c.variant === "h2") return <h3 className={s.h2}>{t}</h3>;
      if (c.variant === "h3") return <h4 className={s.h3}>{t}</h4>;
      return <p className={c.variant === "caption" ? s.caption : s.body}>{t}</p>;
    }
    case "ValueRow":
      return (
        <div className={s.valueRow}>
          <span className={s.label}>{c.label}</span>
          <Value v={resolve(data, c.value.path)} />
          {c.note ? <span className={s.note}>{text(c.note, data)}</span> : null}
        </div>
      );
    case "ApprovalChip":
      return <Approval c={c} ctx={ctx} />;
    case "NextCalcChips": {
      const chips = resolve(data, c.chips.path) as { items?: string[] } | undefined;
      if (!chips?.items?.length) return null;
      return (
        <ul className={s.chips} aria-label="Next calculations">
          {chips.items.map((t) => (
            <li key={t} className={s.chip}>
              {t}
            </li>
          ))}
        </ul>
      );
    }
    default:
      break;
  }
  if (PANEL_SET.has(c.component) && "children" in c && "title" in c) {
    const stamp =
      "stamp" in c && c.stamp ? (resolve(data, c.stamp.path) as StampKind | undefined) : undefined;
    const prov =
      "provenance" in c && c.provenance
        ? (resolve(data, c.provenance.path) as { text?: string; provenanceId?: string } | undefined)
        : undefined;
    return (
      <section className={s.panel} data-kind={c.component} aria-label={text(c.title, data)}>
        <header className={s.panelHead}>
          <h3 className={s.panelTitle}>{text(c.title, data)}</h3>
          {stamp && typeof stamp === "string" ? <Stamp kind={stamp} /> : null}
        </header>
        <div className={s.panelBody}>{kids(c.children, false, false)}</div>
        {prov?.text ? (
          <footer className={s.panelFoot}>
            <Provenance
              text={prov.text}
              copy={prov.provenanceId ?? prov.text}
              label="provenance id"
            />
          </footer>
        ) : null}
      </section>
    );
  }
  if (VIEW_SET.has(c.component) && "data" in c) {
    const value = resolve(data, c.data.path);
    const custom = ctx.views?.[c.component];
    if (custom) return <>{custom(value, c)}</>;
    const title = c.title ? text(c.title, data) : undefined;
    if (
      ["SchemaForm", "ParameterGrid", "ArtifactView", "JobStatus", "EvidenceView"].includes(
        c.component,
      )
    )
      return (
        <WorkspaceView
          kind={c.component}
          value={value}
          title={title}
          imageActivity={ctx.imageActivity}
          scope={ctx.scope ?? ctx.surface.id}
          onAction={ctx.onAction}
          loadArtifact={ctx.loadArtifact}
          uploadArtifact={ctx.uploadArtifact}
          live={ctx.live}
        />
      );
    if (c.component === "ResultTable" || c.component === "MetricGrid")
      return <Results kind={c.component} variant={c.variant} title={title} value={value} />;
    if (c.component === "Chart")
      return isChart(value) ? (
        <ChartView data={value} {...(title ? { title } : {})} />
      ) : (
        <p className={s.placeholder}>No chart data yet</p>
      );
    if (c.component === "Heatmap")
      return isHeatmap(value) ? (
        <HeatmapView data={value} {...(title ? { title } : {})} />
      ) : (
        <p className={s.placeholder}>No field data yet</p>
      );
    if ((c.component as string) === "DataTable")
      return isTable(value) ? (
        <DataTableView data={value} title={title} variant={c.variant} />
      ) : (
        <p className={s.placeholder}>No table data yet</p>
      );
    if ((c.component as string) === "RichText")
      return isRich(value) ? (
        <RichTextView data={value} title={title} />
      ) : (
        <p className={s.placeholder}>No text yet</p>
      );
    if (c.component === "TradeOffFront") return <TradeOff value={value} title={title} />;
    return <ViewFallback c={c} value={value} />;
  }
  return null;
}

function isChart(v: unknown): v is ChartData {
  return !!v && typeof v === "object" && Array.isArray((v as ChartData).series);
}

function isTable(v: unknown): v is DataTableData {
  const d = v as DataTableData | null;
  return !!d && typeof d === "object" && Array.isArray(d.columns) && Array.isArray(d.rows);
}

function isRich(v: unknown): v is RichTextData {
  return !!v && typeof v === "object" && Array.isArray((v as RichTextData).segments);
}

function isHeatmap(v: unknown): v is HeatmapData {
  const d = v as HeatmapData | null;
  return (
    !!d &&
    typeof d === "object" &&
    Array.isArray(d.values) &&
    d.values.length > 0 &&
    Array.isArray(d.values[0]) &&
    typeof d.min === "number" &&
    typeof d.max === "number"
  );
}

type ResultItem = {
  name: string;
  quantity: unknown;
  status?: string;
  provenance?: { text: string; ledgerId?: string };
};

/** ResultTable (a table, or a compact one) and MetricGrid (tiles, or a strip) over the same
 * items. Values come from the data model with their stamp and status; nothing is computed. */
function Results({
  kind,
  variant,
  title,
  value,
}: {
  kind: "ResultTable" | "MetricGrid";
  variant: string | undefined;
  title: string | undefined;
  value: unknown;
}) {
  const body = (value ?? {}) as { title?: string; items?: ResultItem[] };
  const items = body.items ?? [];
  const heading = title || body.title || "Results";
  if (!items.length)
    return (
      <section className={s.results}>
        <h3 className={s.panelTitle}>{heading}</h3>
        <p className={s.placeholder}>No computed result yet</p>
      </section>
    );
  if (kind === "MetricGrid")
    return (
      <section className={s.results} aria-label={heading}>
        <h3 className={s.panelTitle}>{heading}</h3>
        <ul className={s.tiles} data-variant={variant ?? "tiles"}>
          {items.map((item) => (
            <li className={s.tile} key={item.name} data-status={item.status ?? "current"}>
              <span className={s.tileLabel}>{item.name}</span>
              <span className={s.tileValue}>
                <Metric v={item.quantity} />
              </span>
              <span className={s.tileFoot}>
                {isQuantity(item.quantity) && item.quantity.stamp ? (
                  <Stamp kind={item.quantity.stamp as StampKind} />
                ) : null}
                {item.status && item.status !== "current" ? (
                  <span className={s.status}>{item.status}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      </section>
    );
  return (
    <section className={s.results} aria-label={heading}>
      <h3 className={s.panelTitle}>{heading}</h3>
      <div className={s.tableScroll}>
        <table className={s.resultTable} data-variant={variant ?? "table"}>
          <thead>
            <tr>
              <th scope="col">Result</th>
              <th scope="col">Value</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.name} data-status={item.status ?? "current"}>
                <th scope="row">
                  <span title={item.name}>{item.name.replaceAll("_", " ")}</span>
                  {item.provenance ? (
                    <span className={s.prov}>
                      {item.provenance.text}
                      {item.provenance.ledgerId ? ` · ${item.provenance.ledgerId}` : ""}
                    </span>
                  ) : null}
                </th>
                <td>
                  <Value v={item.quantity} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** A tile's value: a large number with its unit, or the generic value for other kinds. */
function Metric({ v }: { v: unknown }) {
  if (isQuantity(v))
    return (
      <>
        <span className={s.metricNum}>{fmt(v.v)}</span>
        {v.unit ? <span className={s.metricUnit}>{unitLabel(v.unit)}</span> : null}
        {v.illustrative ? <span className={s.ill}>illustrative</span> : null}
      </>
    );
  return <Value v={v} />;
}

type Option = Record<string, unknown>;

/** A trade-off the swarm opened: its status, and the options it is weighing when it lists
 * them. The values arrive from the room with their provenance; this only lays them out. */
function TradeOff({ value, title }: { value: unknown; title: string | undefined }) {
  if (!value || typeof value !== "object") return <p className={s.placeholder}>No trade-off yet</p>;
  const t = value as Record<string, unknown>;
  const list = ["front", "candidates", "options", "points"]
    .map((k) => t[k])
    .find((v): v is Option[] => Array.isArray(v) && v.every((o) => !!o && typeof o === "object"));
  const status = typeof t.status === "string" ? t.status : "";
  const scalar = (o: Option) =>
    Object.entries(o).filter(
      ([k, v]) => k !== "provenanceId" && (typeof v === "number" || typeof v === "string"),
    );
  return (
    <section className={s.results} aria-label={title || "Trade-off"}>
      <header className={s.panelHead}>
        <h3 className={s.panelTitle}>
          {title || (typeof t.title === "string" ? t.title : "Trade-off")}
        </h3>
        {status ? <span className={s.status}>{status}</span> : null}
      </header>
      {typeof t.blockedReason === "string" ? <p className={s.note}>{t.blockedReason}</p> : null}
      {list?.length ? (
        <ol className={s.options}>
          {list.map((o, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: options carry no id
            <li key={i}>
              {scalar(o).map(([k, v]) => (
                <span key={k} className={s.option}>
                  <span className={s.label}>{k}</span>
                  <span className={s.num}>{typeof v === "number" ? fmt(v) : String(v)}</span>
                </span>
              ))}
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function ViewFallback({ c, value }: { c: Component; value: unknown }) {
  const title = "title" in c && typeof c.title === "string" ? c.title : null;
  if (c.component === "WarningChip" && value && typeof value === "object") {
    const w = value as { message?: string; author?: string };
    return (
      <div className={s.warning} role="note">
        <Icon name="warning" size={16} />
        <span>
          {w.message}
          {w.author ? <span className={s.note}> · {w.author}</span> : null}
        </span>
      </div>
    );
  }
  if (c.component === "GhostProposal" && value && typeof value === "object") {
    const p = value as {
      author?: string;
      lane?: string;
      owner?: string | null;
      reason?: string;
      status?: string;
    };
    const readOnly = "variant" in c && c.variant === "read-only";
    const open = !p.status || p.status === "awaiting-owner";
    return (
      <div className={s.ghost} data-variant={readOnly ? "read-only" : "waiting-for-owner"}>
        <Icon name="proposal" size={16} />
        <span>
          {p.author} {readOnly && !open ? "proposed" : "proposes"} in {p.lane}: {p.reason}
          <span className={s.note}>
            {" "}
            · {open ? `waiting for ${p.owner ?? "an owner"}` : p.status}
          </span>
        </span>
      </div>
    );
  }
  if (c.component === "DecisionLog" && value && typeof value === "object") {
    const entries =
      (value as { entries?: { seq: number; kind: string; actor: string }[] }).entries ?? [];
    if (!entries.length) return null;
    return (
      <section className={s.decisionPanel} aria-label={title ?? "Room activity"}>
        <h3 className={s.decisionHead}>{title ?? "Room activity"}</h3>
        <ol className={s.decisionLog}>
          {entries.slice(-6).map((e) => (
            <li key={e.seq} title={`#${e.seq} · ${e.kind}`}>
              {logLine(e.actor, e.kind)}
            </li>
          ))}
        </ol>
      </section>
    );
  }
  return (
    <div className={s.fieldBox}>
      <span>{title ?? c.component}</span>
    </div>
  );
}

function Approval({ c, ctx }: { c: Extract<Component, { component: "ApprovalChip" }>; ctx: Ctx }) {
  const p = resolve(ctx.surface.data, c.proposal.path) as
    | { id?: string; author?: string; lane?: string; reason?: string }
    | undefined;
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = c.action.event.context?.proposalId ?? p?.id;
  if (!p || !id) return null;
  async function decide(accept: boolean) {
    if (!ctx.onAction || !id) return;
    if (!reason.trim()) {
      setError("Give a reason; it becomes a lesson for the agent.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await ctx.onAction({ name: "decide", proposalId: id, accept, reason: reason.trim() });
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={s.approval}>
      <div className={s.approvalHead}>
        <Icon name="proposal" size={18} />
        <span>
          <strong>{p.author}</strong> proposes in your {p.lane} lane
        </span>
      </div>
      <p className={s.body}>{p.reason}</p>
      <label className={s.reason}>
        <span className="visually-hidden">Your reason</span>
        <input
          value={reason}
          placeholder="Your reason (it becomes a lesson)"
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      <div className={s.approvalBtns}>
        <button
          type="button"
          className={s.accept}
          disabled={busy || !ctx.onAction}
          onClick={() => decide(true)}
        >
          Accept
        </button>
        <button
          type="button"
          className={s.decline}
          disabled={busy || !ctx.onAction}
          onClick={() => decide(false)}
        >
          Decline
        </button>
      </div>
      {error ? (
        <p className={s.err} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
