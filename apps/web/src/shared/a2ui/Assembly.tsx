import {
  type CSSProperties,
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import a from "./assembly.module.css";
import { resolve } from "./surface";
import { type Component, DATA_VIEWS, PANELS, type Surface } from "./types";

/** The agent renders a workspace panel by panel. A surface arrives whole; the client stages it:
 * skeletons shaped like the coming panels, then each panel materialises in reading order
 * (about 120-150 ms apart), the one being placed named in the agent's seat colour, then rest.
 * Only panels the person has not seen yet animate, so an update never replays the sequence.
 * With reduced motion, or when no staging is asked for, everything shows at once. */

export type Phase = "skeleton" | "materialising" | "live" | "rest";
export type Unit = { id: string; label: string; kind: string };

/** First pause, so the skeletons are seen before the first panel lands. */
export const SKELETON_MS = 90;
/** The whole staging is paced to stay under this, so nothing waits long to be usable. */
export const BUDGET_MS = 1000;
export const MAX_STEP_MS = 150;
export const MIN_STEP_MS = 40;
const SETTLE_MS = 320;

const BLOCKS = new Set<string>([...PANELS, ...DATA_VIEWS, "DataTable", "RichText", "ApprovalChip"]);
const CONTAINERS = new Set(["Column", "Row", "Card", "WorkspaceLayout"]);
const KIND_LABEL: Record<string, string> = {
  SchemaForm: "Controls",
  ParameterGrid: "Parameters",
  ArtifactView: "Model space",
  JobStatus: "Run",
  EvidenceView: "Evidence",
  ResultTable: "Results",
  MetricGrid: "Results",
  Chart: "Chart",
  Heatmap: "Field",
  DataTable: "Table",
  RichText: "Notes",
  ApprovalChip: "Proposal",
  TradeOffFront: "Trade-off",
};

function labelOf(c: Component, data: unknown): string {
  if ("title" in c && c.title !== undefined) {
    if (typeof c.title === "string") return c.title;
    const r = resolve(data, c.title.path);
    if (typeof r === "string" && r) return r;
  }
  if ("data" in c) {
    const v = resolve(data, c.data.path);
    if (v && typeof v === "object" && typeof (v as { title?: unknown }).title === "string") {
      const t = (v as { title: string }).title;
      if (t) return t;
    }
  }
  return KIND_LABEL[c.component] ?? "panel";
}

/** The panels of a surface in reading order. Panels in a hidden tab are not staged: they are
 * not on screen, so waiting for them would only cost time. */
export function unitsOf(surface: Surface): Unit[] {
  const out: Unit[] = [];
  const seen = new Set<string>();
  const walk = (id: string, depth: number) => {
    const c = surface.components[id];
    if (!c || depth > 24 || seen.has(id)) return;
    seen.add(id);
    if (BLOCKS.has(c.component)) {
      out.push({ id, label: labelOf(c, surface.data), kind: c.component });
      return;
    }
    if (!CONTAINERS.has(c.component)) return;
    const kids = "child" in c ? [c.child] : "children" in c ? c.children : ([] as string[]);
    const tabs = c.component === "WorkspaceLayout" && c.kind === "tabs";
    for (const k of tabs ? kids.slice(0, 1) : kids) walk(k, depth + 1);
  };
  walk("root", 0);
  return out;
}

export function prefersReducedMotion(): boolean {
  try {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return true;
  }
}

export function stepFor(count: number, budget: number = BUDGET_MS): number {
  return Math.max(MIN_STEP_MS, Math.min(MAX_STEP_MS, Math.floor(budget / Math.max(1, count))));
}

/** Pacing a caller can change. The assembly frame holds the first panel until its skeleton boxes
 * have landed on the plan (`delayMs`) and keeps the whole reveal short (`budgetMs`); `quiet`
 * leaves an unplaced panel empty, because the frame's boxes already stand where it will be. */
export type AssemblyOptions = { delayMs?: number; budgetMs?: number; quiet?: boolean };

type State = {
  revealed: ReadonlySet<string>;
  placing: string | null;
  justPlaced: string | null;
  phase: Phase;
};
const EMPTY: State = { revealed: new Set(), placing: null, justPlaced: null, phase: "skeleton" };

export type AssemblyValue = {
  units: ReadonlyMap<string, Unit>;
  revealed: ReadonlySet<string>;
  placing: Unit | null;
  justPlaced: string | null;
  phase: Phase;
  total: number;
  agent: string;
  quiet: boolean;
};
const Ctx = createContext<AssemblyValue | null>(null);

/** Stages the units of a surface. Null when staging is off: the caller shows everything at once. */
export function useAssembly(
  units: Unit[],
  enabled: boolean,
  agent: string,
  options: AssemblyOptions = {},
): AssemblyValue | null {
  const { delayMs = SKELETON_MS, budgetMs = BUDGET_MS, quiet = false } = options;
  const [reduced] = useState(prefersReducedMotion);
  const off = !enabled || reduced;
  const [state, setState] = useState<State>(EMPTY);
  const ref = useRef<State>(EMPTY);
  const key = units.map((u) => u.id).join("|");
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` stands for the unit ids
  useEffect(() => {
    if (off) return;
    const pending = units.filter((u) => !ref.current.revealed.has(u.id));
    const put = (next: State) => {
      ref.current = next;
      setState(next);
    };
    if (!pending.length) {
      if (ref.current.phase === "skeleton") put({ ...ref.current, phase: "rest" });
      return;
    }
    const first = ref.current.revealed.size === 0;
    const step = stepFor(pending.length, budgetMs);
    let i = 0;
    let timer: ReturnType<typeof setTimeout>;
    put({
      ...ref.current,
      placing: pending[0]?.id ?? null,
      phase: first ? "skeleton" : "materialising",
    });
    const tick = () => {
      const unit = pending[i];
      if (!unit) return;
      i += 1;
      put({
        revealed: new Set(ref.current.revealed).add(unit.id),
        justPlaced: unit.id,
        placing: pending[i]?.id ?? null,
        phase: "materialising",
      });
      if (i < pending.length) {
        timer = setTimeout(tick, step);
        return;
      }
      timer = setTimeout(() => {
        put({ ...ref.current, placing: null, phase: "live" });
        timer = setTimeout(
          () => put({ ...ref.current, justPlaced: null, phase: "rest" }),
          SETTLE_MS,
        );
      }, step);
    };
    timer = setTimeout(tick, first ? delayMs : 0);
    return () => clearTimeout(timer);
  }, [key, off, delayMs, budgetMs]);
  const map = useMemo(() => new Map(units.map((u) => [u.id, u])), [units]);
  if (off) return null;
  return {
    units: map,
    revealed: state.revealed,
    placing: state.placing ? (map.get(state.placing) ?? null) : null,
    justPlaced: state.justPlaced,
    phase: state.phase,
    total: units.length,
    agent,
    quiet,
  };
}

export function AssemblyProvider({
  value,
  children,
}: {
  value: AssemblyValue | null;
  children: ReactNode;
}) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Wraps one panel. Not yet placed: a skeleton of its shape. Placed: the panel, fading in. */
export function Reveal({ id, children }: { id: string; children: ReactNode }) {
  const ctx = useContext(Ctx);
  const unit = ctx?.units.get(id);
  if (!ctx || !unit) return <>{children}</>;
  const revealed = ctx.revealed.has(id);
  if (!revealed && !ctx.quiet)
    return <Skeleton kind={unit.kind} label={unit.label} placing={ctx.placing?.id === id} />;
  // Quiet (on the assembly stage): an unplaced panel is laid out but veiled, so the page already
  // has its final size and the stage's boxes land on it. The same element is kept when it is
  // placed, so the panel (an app, an image) is not mounted twice.
  return (
    <div
      className={a.reveal}
      data-unit={id}
      data-reveal={revealed ? "true" : undefined}
      data-veiled={revealed ? undefined : "true"}
      data-testid={revealed ? undefined : "assembly-hold"}
      aria-hidden={revealed ? undefined : true}
      inert={!revealed}
      data-placed={ctx.justPlaced === id ? "true" : undefined}
    >
      {children}
    </div>
  );
}

function Skeleton({ kind, label, placing }: { kind: string; label: string; placing: boolean }) {
  return (
    <div
      className={a.skeleton}
      data-kind={kind}
      data-placing={placing || undefined}
      data-testid="assembly-skeleton"
      aria-hidden
    >
      <span className={a.skHead} />
      <span className={a.skLine} />
      <span className={a.skLine} data-short="true" />
      <span className={a.skBlock} />
      {placing ? <span className={a.skCaption}>placing {label}…</span> : null}
    </div>
  );
}

/** One line above the surface: who is placing what. It folds away at rest. */
export function AssemblyBar() {
  const ctx = useContext(Ctx);
  if (!ctx || ctx.total === 0) return null;
  let shown = 0;
  for (const k of ctx.units.keys()) if (ctx.revealed.has(k)) shown += 1;
  const text =
    ctx.phase === "skeleton"
      ? `${ctx.agent} is laying out the workspace…`
      : ctx.phase === "materialising"
        ? ctx.placing
          ? `${ctx.agent} · placing ${ctx.placing.label}…`
          : `${ctx.agent} · finishing…`
        : ctx.phase === "live"
          ? "Workspace placed"
          : "";
  return (
    <div className={a.bar} data-phase={ctx.phase} data-testid="assembly-bar" aria-hidden>
      <div className={a.barInner}>
        <span className={a.dot} />
        <span className={a.barText}>{text}</span>
        <span className={a.barCount}>
          {shown}/{ctx.total}
        </span>
      </div>
    </div>
  );
}

/** The seat colour the ring and caption use: the requesting seat's, else the conductor's grey. */
export function accentStyle(accent: string | undefined): CSSProperties {
  return { ["--assemble-accent" as string]: accent ?? "var(--ink-3)" } as CSSProperties;
}

/** The empty frame, before any surface exists: skeletons shaped like a controls column beside a
 * model view, with the agent's colour on the one being laid out. */
export function FrameSkeleton({ accent }: { accent?: string }) {
  return (
    <div className={a.frame} style={accentStyle(accent)} data-testid="frame-skeleton" aria-hidden>
      <Skeleton kind="SchemaForm" label="Controls" placing />
      <Skeleton kind="ArtifactView" label="Model space" placing={false} />
    </div>
  );
}
