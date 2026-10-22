import {
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { prefersReducedMotion } from "./Assembly";
import f from "./assembly-stage.module.css";
import { column, FRAME_GAP, row, type Size } from "./planGeometry";
import type { Surface } from "./types";

/** The workspace assembling straight on the canvas: no frame, no fixed height.
 *
 * While the agent composes, soft boxes stand on the canvas and move into another plausible
 * arrangement every `CYCLE_MS` (each box animates left/top/width/height over `MORPH_MS`). When the
 * composed plan arrives, the real surface mounts at its natural size with every panel veiled
 * (laid out, not visible), the boxes move onto the measured rectangle of each panel, and the panels
 * then appear in place one after another (the reveal in Assembly.tsx). Nothing is bounded: the
 * finished workspace takes the height it needs on the canvas. With reduced motion the skeleton is
 * one static layout and the surface shows at once. */

export type StageState = "assembling" | "ready" | "blocked";
export const CYCLE_MS = 1800;
export const MORPH_MS = 600;
/** Where the reveal starts after the plan arrives: the boxes are nearly in place. */
export const LAND_DELAY_MS = 520;
/** The reveal after landing stays this short, so landing plus reveal stays near 1.4 s. */
export const LAND_BUDGET_MS = 800;
const FADE_OUT_MS = 260;
/** If the surface never reports it is placed, stop covering it after this long. */
const SAFETY_MS = 3000;
const SLOTS = 8;
const FALLBACK: Size = { w: 1000, h: 600 };

export type Rect = { x: number; y: number; w: number; h: number };
// A template is a tree: "v" stacks its parts, "h" sets them side by side; a part is a weight and
// either a box (null) or a nested tree.
type Part = [number, Tree | null];
type Tree = { dir: "h" | "v"; parts: Part[] };
const leaf = (w: number): Part => [w, null];
const tree = (dir: "h" | "v", ...parts: Part[]): Tree => ({ dir, parts });

/** Plausible studios: control desk beside a model space; three columns; a wide top and three
 * below; a tabbed main with a side column; a centred model flanked by two stacks. */
const TEMPLATES: Tree[] = [
  tree(
    "h",
    [1, tree("v", leaf(1), leaf(1), leaf(1), leaf(1))],
    [2.2, tree("v", [2.4, null], [1, tree("h", leaf(1), leaf(1), leaf(1))])],
  ),
  tree(
    "h",
    [1, tree("v", leaf(1), leaf(1), leaf(1))],
    [1, tree("v", leaf(1), leaf(1))],
    [1, tree("v", leaf(1), leaf(1))],
  ),
  tree(
    "v",
    [0.55, null],
    [2.2, tree("h", [2, null], [1, tree("v", leaf(1), leaf(1))])],
    [1.2, tree("h", leaf(1), leaf(1), leaf(1))],
  ),
  tree(
    "h",
    [2.4, tree("v", [0.35, tree("h", leaf(1), leaf(1), leaf(1), leaf(1))], [3, null])],
    [1, tree("v", leaf(1), leaf(1), leaf(1))],
  ),
  tree(
    "h",
    [1, tree("v", leaf(1), leaf(1))],
    [1.5, tree("v", leaf(1.4), [1, tree("h", leaf(1), leaf(1))])],
    [1, tree("v", leaf(1), leaf(1))],
  ),
];

function expand(t: Tree, box: Rect, gap: number, out: Rect[]) {
  const weights = t.parts.map((p) => p[0]);
  const cut = (t.dir === "h" ? row : column)(box, weights, gap);
  t.parts.forEach(([, child], i) => {
    const b = cut[i];
    if (!b) return;
    if (child) expand(child, b, gap, out);
    else out.push(b);
  });
}

/** The boxes of one template, in pixels of the skeleton area. */
export function templateRects(index: number, size: Size, gap: number = FRAME_GAP): Rect[] {
  const t = TEMPLATES[((index % TEMPLATES.length) + TEMPLATES.length) % TEMPLATES.length];
  const out: Rect[] = [];
  if (t) expand(t, { x: 0, y: 0, w: size.w, h: size.h }, gap, out);
  return out;
}

/** Where each outermost panel of the mounted surface sits, relative to `host`. Panels are the
 * elements the reveal marks with `data-unit`; a panel inside another one is part of it. */
export function measurePanels(host: HTMLElement): Rect[] {
  const origin = host.getBoundingClientRect();
  const units = Array.from(host.querySelectorAll<HTMLElement>("[data-unit]"));
  return units
    .filter((el) => !el.parentElement?.closest("[data-unit]"))
    .map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height };
    })
    .filter((r) => r.w > 0 && r.h > 0);
}

const centre = (r: Rect): Rect => ({ x: r.x + r.w / 2, y: r.y + r.h / 2, w: 0, h: 0 });

type Slot = { rect: Rect; visible: boolean };

/** The boxes for the current layout. Boxes without a rectangle collapse into the nearest one (they
 * fade out, or grow out of it when the plan has more panels than the template). */
function slotsFor(rects: Rect[], count: number): Slot[] {
  const last = rects[rects.length - 1];
  return Array.from({ length: count }, (_, i) => {
    const r = rects[i];
    if (r) return { rect: r, visible: true };
    const near = rects[i % Math.max(1, rects.length)] ?? last;
    return { rect: near ? centre(near) : { x: 0, y: 0, w: 0, h: 0 }, visible: false };
  });
}

export type StageSurfaceProps = {
  quiet: boolean;
  startDelayMs: number;
  budgetMs: number;
  onAssembled: () => void;
};

function useSize(ref: React.RefObject<HTMLElement | null>, active: boolean): Size {
  const [size, setSize] = useState<Size>(FALLBACK);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !active) return;
    const read = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0)
        setSize((p) => (p.w === r.width && p.h === r.height ? p : { w: r.width, h: r.height }));
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, active]);
  return size;
}

export function AssemblyStage({
  title,
  intent,
  pill,
  state,
  surface,
  accent,
  notes = [],
  message,
  children,
}: {
  title: string;
  intent?: string;
  /** The status chip once the workspace stands, such as "Agent composed". */
  pill: string;
  state: StageState;
  /** The composed surface once it is ready. */
  surface?: Surface | undefined;
  /** The requesting seat's colour, for the status dot. */
  accent?: string | undefined;
  /** What the composer has really done so far, in order; the last one is the current step. */
  notes?: string[];
  /** Shown on the canvas when no composition is coming (a model is needed, or it failed). */
  message?: ReactNode;
  children: (p: StageSurfaceProps) => ReactNode;
}) {
  const [reduced] = useState(prefersReducedMotion);
  const bodyRef = useRef<HTMLDivElement>(null);
  const ready = state === "ready" && !!surface;
  // `direct`: the surface was already there when the studio opened, so nothing morphs.
  const [direct, setDirect] = useState(ready);
  const [stage, setStage] = useState<"skeleton" | "landing" | "settled">(
    ready ? "settled" : "skeleton",
  );
  const [template, setTemplate] = useState(0);
  const [grown, setGrown] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [plan, setPlan] = useState<Rect[]>([]);
  const size = useSize(bodyRef, stage === "skeleton");

  // From the first moment: cycle the arrangement until the plan arrives.
  useEffect(() => {
    if (stage !== "skeleton" || reduced || state !== "assembling") return;
    const t = setInterval(() => setTemplate((n) => n + 1), CYCLE_MS);
    return () => clearInterval(t);
  }, [stage, reduced, state]);

  // The plan arrives (or goes away again when the agent recomposes).
  useEffect(() => {
    if (ready) {
      setStage((s) => (s === "skeleton" ? (reduced || direct ? "settled" : "landing") : s));
      return;
    }
    // The plan is gone (the agent recomposes) or never came: back to the skeleton.
    setStage("skeleton");
    setLeaving(false);
    setGrown(false);
    setDirect(false);
    setPlan([]);
  }, [ready, reduced, direct]);

  // Landing: the surface is mounted with its panels veiled; measure where they really are, and
  // again whenever the surface changes size (an image or app finishing its layout).
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (stage !== "landing" || !el) return;
    const read = () => setPlan(measurePanels(el));
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [stage]);

  useEffect(() => {
    if (stage !== "landing") return;
    const g = setTimeout(() => setGrown(true), 30);
    const safety = setTimeout(() => setStage("settled"), SAFETY_MS);
    return () => {
      clearTimeout(g);
      clearTimeout(safety);
    };
  }, [stage]);

  // The surface has placed its last panel: fade the boxes away and drop them.
  const assembled = useCallback(() => {
    setStage((s) => (s === "landing" ? "settled" : s));
  }, []);
  useEffect(() => {
    if (stage !== "settled" || !leaving) return;
    const t = setTimeout(() => setLeaving(false), FADE_OUT_MS);
    return () => clearTimeout(t);
  }, [stage, leaving]);
  const prevStage = useRef(stage);
  useEffect(() => {
    if (prevStage.current === "landing" && stage === "settled") setLeaving(true);
    prevStage.current = stage;
  }, [stage]);

  const landing = stage === "landing" && plan.length > 0;
  // The boxes stand on the panels while landing and while they fade away.
  const onPlan = (landing || leaving) && plan.length > 0;
  const count = Math.max(SLOTS, plan.length);
  const slots = useMemo(() => {
    const base = templateRects(reduced ? 0 : template, size);
    if (!onPlan) return slotsFor(base, count);
    // Before `grown`, boxes the template does not have start collapsed on a nearby box.
    return slotsFor(plan, count).map((slot, i) => {
      if (grown || i < SLOTS || !slot.visible) return slot;
      const near = base[i % Math.max(1, base.length)];
      return near ? { rect: centre(near), visible: true } : slot;
    });
  }, [template, size, reduced, onPlan, plan, grown, count]);

  const showBoxes = state !== "blocked" && (stage !== "settled" || leaving);
  const live = state === "assembling" || stage === "landing";
  const elapsed = useElapsed(live);
  const current = notes[notes.length - 1];

  return (
    <section
      className={f.stage}
      data-state={state}
      data-stage={stage}
      data-reduced={reduced ? "true" : undefined}
      style={{ ["--stage-accent" as string]: accent ?? "var(--ink-3)" } as CSSProperties}
      aria-label="Workspace"
      aria-busy={live || undefined}
      data-testid="assembly-stage"
    >
      <header className={f.head}>
        <div className={f.titles}>
          <h1 className={f.title}>{title}</h1>
          {intent ? <p className={f.intent}>{intent}</p> : null}
        </div>
        <span
          className={f.pill}
          role="status"
          data-live={live ? "true" : undefined}
          title={live && notes.length > 1 ? notes.join(" · ") : undefined}
          data-testid="assembly-status"
        >
          {live ? (
            <>
              <span className={f.dot} aria-hidden />
              <span className={f.step}>{current ?? "Waiting for your agent"}</span>
              <span className={f.elapsed} data-testid="assembly-elapsed">
                {elapsed} s
              </span>
            </>
          ) : (
            pill
          )}
        </span>
      </header>
      <div className={f.body} ref={bodyRef} data-skeleton={stage === "skeleton" || undefined}>
        {state === "blocked" ? <div className={f.message}>{message}</div> : null}
        {ready && stage !== "skeleton" ? (
          <div className={f.surface} data-testid="stage-surface">
            {children({
              quiet: stage === "landing",
              startDelayMs: direct ? 90 : LAND_DELAY_MS,
              budgetMs: direct ? 1000 : LAND_BUDGET_MS,
              onAssembled: assembled,
            })}
          </div>
        ) : null}
        {showBoxes ? (
          <div
            className={f.boxes}
            data-leaving={leaving ? "true" : undefined}
            data-landing={landing ? "true" : undefined}
            aria-hidden
            data-testid="stage-boxes"
          >
            {slots.map((slot, i) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: slots are positional
                key={i}
                className={f.box}
                data-testid="stage-box"
                data-slot={i}
                data-visible={slot.visible ? "true" : "false"}
                data-parity={template % 2}
                style={{
                  left: `${slot.rect.x}px`,
                  top: `${slot.rect.y}px`,
                  width: `${slot.rect.w}px`,
                  height: `${slot.rect.h}px`,
                  opacity: slot.visible ? 1 : 0,
                }}
              />
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}

/** Whole seconds since the workspace started assembling. */
function useElapsed(active: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    setN(0);
    const t = setInterval(() => setN((v) => v + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
  return n;
}
