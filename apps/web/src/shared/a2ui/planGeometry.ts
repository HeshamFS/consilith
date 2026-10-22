import { unitsOf } from "./Assembly";
import type { Component, Surface } from "./types";

/** The geometry of a composed workspace, for the bounded renderer (Renderer.tsx with `bounded`,
 * the CSS in workspace-layout.module.css and a2ui.module.css). The assembly stage
 * (AssemblyStage.tsx) uses `row` and `column` for its skeleton templates and measures the real
 * panels instead:
 *
 * - a stack or a column divides its height by weight; a row or split divides its width by the
 *   ratio; a grid uses `gridCols` columns of equal rows; tabs show the first panel under the
 *   tab strip;
 * - every gutter is `FRAME_GAP` px; a titled layout spends `HEADING_PX` on its eyebrow, a tab strip
 *   `TABS_PX`, and a surface, hero or quiet layout pads its content. */

export const FRAME_GAP = 16;
export const HEADING_PX = 28;
export const TABS_PX = 42;
/** Under this width a split stacks, as in the CSS (`@container a2ui`). */
export const STACK_BELOW_PX = 720;
const PAD: Record<string, number> = { surface: 16, hero: 16, quiet: 12, plain: 0 };

export type Size = { w: number; h: number };
/** One block's rectangle in percent of the frame. */
export type PlanBox = {
  id: string;
  label: string;
  kind: string;
  x: number;
  y: number;
  w: number;
  h: number;
};
type Box = { x: number; y: number; w: number; h: number };

const LEAF_WEIGHT: Record<string, number> = {
  SchemaForm: 3.2,
  ParameterGrid: 2.4,
  ArtifactView: 3.2,
  JobStatus: 0.75,
  EvidenceView: 1.9,
  ResultTable: 1.9,
  MetricGrid: 0.7,
  Chart: 2,
  Heatmap: 2.4,
  DataTable: 2,
  RichText: 1.4,
};
const SMALL = 0.3;

type LayoutC = Extract<Component, { component: "WorkspaceLayout" }>;

/** How much of a column's height this node asks for. A plan's own `weight` wins. */
export function weightOf(surface: Surface, id: string, depth = 0): number {
  const c = surface.components[id];
  if (!c || depth > 24) return SMALL;
  if (typeof c.weight === "number" && c.weight > 0) return c.weight;
  const kids = (ids: string[]) => ids.map((k) => weightOf(surface, k, depth + 1));
  switch (c.component) {
    case "Column":
      return kids(c.children).reduce((a, b) => a + b, 0) || SMALL;
    case "Row":
      return Math.max(SMALL, ...kids(c.children));
    case "Card":
      return weightOf(surface, c.child, depth + 1);
    case "WorkspaceLayout": {
      const w = kids(c.children);
      if (c.kind === "stack" || c.kind === "section") return w.reduce((a, b) => a + b, 0) || SMALL;
      if (c.kind === "grid") {
        const cols = gridCols(w.length);
        return Math.max(SMALL, ...w) * Math.ceil(w.length / cols);
      }
      return Math.max(SMALL, ...w);
    }
    default:
      return (
        LEAF_WEIGHT[c.component] ??
        (c.component === "Text" || c.component === "Divider" ? SMALL : 1.5)
      );
  }
}

export function gridCols(n: number): number {
  return n <= 2 ? Math.max(1, n) : n <= 4 ? 2 : 3;
}

export function splitFractions(ratio: LayoutC["ratio"] | undefined, n: number): number[] {
  const out = Array.from({ length: Math.max(1, n) }, () => 1);
  if (n >= 2 && ratio === "focus-start") out[0] = 1.65;
  if (n >= 2 && ratio === "focus-end") out[n - 1] = 1.65;
  return out;
}

/** CSS grid-template-columns for a split, in the same fractions as `splitFractions`. */
export function splitTemplate(ratio: LayoutC["ratio"] | undefined, n: number): string {
  return splitFractions(ratio, n)
    .map((f) => `minmax(0, ${f}fr)`)
    .join(" ");
}

/** Cut `total` into parts by weight with `gap` between them. */
export function divide(total: number, weights: number[], gap: number): [number, number][] {
  const sum = weights.reduce((a, b) => a + b, 0) || 1;
  const free = Math.max(0, total - gap * Math.max(0, weights.length - 1));
  let at = 0;
  return weights.map((w) => {
    const size = (free * w) / sum;
    const part: [number, number] = [at, size];
    at += size + gap;
    return part;
  });
}

export function column(box: Box, weights: number[], gap: number): Box[] {
  return divide(box.h, weights, gap).map(([y, h]) => ({ x: box.x, y: box.y + y, w: box.w, h }));
}
export function row(box: Box, weights: number[], gap: number): Box[] {
  return divide(box.w, weights, gap).map(([x, w]) => ({ x: box.x + x, y: box.y, w, h: box.h }));
}

/** The workspace's own root: the first child of the surface root. Whatever the room adds after
 * it (a proposal, a warning, the log) sits below the frame and scrolls. */
export function frameRoot(surface: Surface): string {
  const root = surface.components.root;
  return root && root.component === "Column" && root.children[0] ? root.children[0] : "root";
}

const r3 = (n: number) => Math.round(n * 1000) / 1000;

/** The rectangle of every block of the surface, in reading order, in percent of a frame of `size`
 * px. With `gap` 0 the rectangles tile the frame exactly. */
export function planRects(
  surface: Surface,
  size: Size = { w: 1000, h: 600 },
  gap: number = FRAME_GAP,
): PlanBox[] {
  const units = new Map(unitsOf(surface).map((u) => [u.id, u]));
  const out: PlanBox[] = [];
  const seen = new Set<string>();
  const pct = (b: Box) => ({
    x: r3((b.x / size.w) * 100),
    y: r3((b.y / size.h) * 100),
    w: r3((b.w / size.w) * 100),
    h: r3((b.h / size.h) * 100),
  });
  const place = (id: string, box: Box, depth: number) => {
    const c = surface.components[id];
    if (!c || depth > 24 || seen.has(id)) return;
    seen.add(id);
    const unit = units.get(id);
    if (unit) {
      out.push({ id, label: unit.label, kind: unit.kind, ...pct(box) });
      return;
    }
    const w = (ids: string[]) => ids.map((k) => weightOf(surface, k));
    switch (c.component) {
      case "Column": {
        const parts = column(box, w(c.children), gap);
        c.children.forEach((k, i) => {
          const p = parts[i];
          if (p) place(k, p, depth + 1);
        });
        return;
      }
      case "Row": {
        const parts = row(
          box,
          c.children.map(() => 1),
          gap,
        );
        c.children.forEach((k, i) => {
          const p = parts[i];
          if (p) place(k, p, depth + 1);
        });
        return;
      }
      case "Card":
        place(c.child, box, depth + 1);
        return;
      case "WorkspaceLayout": {
        const pad = PAD[c.emphasis] ?? 0;
        let inner: Box = {
          x: box.x + pad,
          y: box.y + pad,
          w: Math.max(0, box.w - pad * 2),
          h: Math.max(0, box.h - pad * 2),
        };
        if (c.title)
          inner = { ...inner, y: inner.y + HEADING_PX, h: Math.max(0, inner.h - HEADING_PX) };
        const ids = c.children;
        if (c.kind === "tabs") {
          const first = ids[0];
          if (first)
            place(
              first,
              { ...inner, y: inner.y + TABS_PX, h: Math.max(0, inner.h - TABS_PX) },
              depth + 1,
            );
          return;
        }
        let parts: Box[];
        if (c.kind === "split" && inner.w >= STACK_BELOW_PX)
          parts = row(inner, splitFractions(c.ratio, ids.length), gap);
        else if (c.kind === "grid") {
          const cols = gridCols(ids.length);
          const rows = Math.ceil(ids.length / cols);
          const rowBoxes = column(
            inner,
            Array.from({ length: rows }, () => 1),
            gap,
          );
          parts = ids.map((_, i) => {
            const rb = rowBoxes[Math.floor(i / cols)] ?? inner;
            return (
              row(
                rb,
                Array.from({ length: cols }, () => 1),
                gap,
              )[i % cols] ?? rb
            );
          });
        } else parts = column(inner, c.kind === "split" ? ids.map(() => 1) : w(ids), gap);
        ids.forEach((k, i) => {
          const p = parts[i];
          if (p) place(k, p, depth + 1);
        });
        return;
      }
      default:
        return;
    }
  };
  place(frameRoot(surface), { x: 0, y: 0, w: size.w, h: size.h }, 0);
  return out;
}
