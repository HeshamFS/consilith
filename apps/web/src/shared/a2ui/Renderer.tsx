import { type ReactNode, useState } from "react";
import { Icon, Stamp, type StampKind } from "@/shared/ui";
import s from "./a2ui.module.css";
import { ResultValue, type TypedResult } from "./ResultValue";
import { resolve } from "./surface";
import {
  type Component,
  DATA_VIEWS,
  PANELS,
  type Quantity,
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
  surface: Surface;
  scope?: string;
  onAction?: (a: A2UIAction) => Promise<void>;
  views?: Partial<Record<string, (data: unknown, c: Component) => ReactNode>>;
  loadArtifact?: WorkspaceActions["loadArtifact"];
};

const PANEL_SET = new Set<string>(PANELS);
const VIEW_SET = new Set<string>(DATA_VIEWS);

export function A2UISurface({
  surface,
  scope,
  onAction,
  views,
  loadArtifact,
}: {
  surface: Surface;
  scope?: string;
  onAction?: Ctx["onAction"];
  views?: Ctx["views"];
  loadArtifact?: WorkspaceActions["loadArtifact"];
}) {
  const ctx: Ctx = {
    surface,
    ...(scope ? { scope } : {}),
    loadArtifact,
    ...(onAction ? { onAction } : {}),
    ...(views ? { views } : {}),
  };
  return (
    <div className={s.surface} data-surface={surface.id}>
      <Node id="root" ctx={ctx} depth={0} />
    </div>
  );
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

function fmt(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  const a = Math.abs(n);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return n.toExponential(2);
  return Number.parseFloat(n.toPrecision(4)).toString();
}

function unit(u: string): string {
  return u
    .replace(/\*\*2/g, "²")
    .replace(/\*\*3/g, "³")
    .replace(/^um/, "µm")
    .replace(/\bum\b/g, "µm");
}

export function QuantityView({ q }: { q: Quantity }) {
  return (
    <span
      className={s.qty}
      title={`provenance ${q.provenanceId}${q.tier !== undefined ? ` · tier ${q.tier}` : ""}`}
    >
      <span className={s.num}>{fmt(q.v)}</span>
      {q.unit ? <span className={s.unit}>{unit(q.unit)}</span> : null}
      {q.stamp ? <Stamp kind={q.stamp as StampKind} /> : null}
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

function Node({ id, ctx, depth }: { id: string; ctx: Ctx; depth: number }) {
  const c = ctx.surface.components[id];
  if (!c || depth > 24) return null;
  const data = ctx.surface.data;
  const kids = (ids: string[]) =>
    ids.map((k) => <Node key={k} id={k} ctx={ctx} depth={depth + 1} />);
  switch (c.component) {
    case "WorkspaceLayout":
      return <Layout component={c}>{kids(c.children)}</Layout>;
    case "Column":
      return <div className={s.column}>{kids(c.children)}</div>;
    case "Row":
      return <div className={s.row}>{kids(c.children)}</div>;
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
        <div className={s.panelBody}>{kids(c.children)}</div>
        {prov?.text ? (
          <footer className={s.prov} title={prov.provenanceId}>
            {prov.text}
          </footer>
        ) : null}
      </section>
    );
  }
  if (VIEW_SET.has(c.component) && "data" in c) {
    const value = resolve(data, c.data.path);
    const custom = ctx.views?.[c.component];
    if (custom) return <>{custom(value, c)}</>;
    if (
      ["SchemaForm", "ParameterGrid", "ArtifactView", "JobStatus", "EvidenceView"].includes(
        c.component,
      )
    )
      return (
        <WorkspaceView
          kind={c.component}
          value={value}
          scope={ctx.scope ?? ctx.surface.id}
          onAction={ctx.onAction}
          loadArtifact={ctx.loadArtifact}
        />
      );
    if (c.component === "ResultTable" || c.component === "MetricGrid") {
      const items =
        (
          value as {
            items?: {
              name: string;
              quantity: unknown;
              provenance?: { text: string; ledgerId?: string };
            }[];
          } | null
        )?.items ?? [];
      return (
        <section className={s.panel}>
          <h3 className={s.panelTitle}>Results</h3>
          {items.length ? (
            items.map((item) => (
              <div className={s.valueRow} key={item.name}>
                <span>{item.name}</span>
                <Value v={item.quantity} />
                {item.provenance ? (
                  <span className={s.prov}>
                    {item.provenance.text}
                    {item.provenance.ledgerId ? ` · ${item.provenance.ledgerId}` : ""}
                  </span>
                ) : null}
              </div>
            ))
          ) : (
            <p className={s.placeholder}>No computed result yet</p>
          )}
        </section>
      );
    }
    return <ViewFallback c={c} value={value} />;
  }
  return null;
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
    const p = value as { author?: string; lane?: string; owner?: string | null; reason?: string };
    return (
      <div className={s.ghost}>
        <Icon name="proposal" size={16} />
        <span>
          {p.author} proposes in {p.lane}: {p.reason}
          <span className={s.note}> · waiting for {p.owner ?? "an owner"}</span>
        </span>
      </div>
    );
  }
  if (c.component === "DecisionLog" && value && typeof value === "object") {
    const entries =
      (value as { entries?: { seq: number; kind: string; actor: string }[] }).entries ?? [];
    if (!entries.length) return null;
    return (
      <ol className={s.decisionLog} aria-label={title ?? "Recent room log"}>
        {entries.slice(-6).map((e) => (
          <li key={e.seq}>
            <span className={s.note}>#{e.seq}</span> {e.actor} · {e.kind}
          </li>
        ))}
      </ol>
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
