import { type CSSProperties, useEffect, useId, useMemo, useRef, useState } from "react";
import { Stamp } from "@/shared/ui";
import s from "./chart.module.css";
import {
  cssRgb,
  niceStep,
  parseColor,
  type Rgb,
  rampAt,
  tickLabel,
  ticksWithin,
} from "./chartMath";
import { FlagBanner, seriesFlags } from "./FlagBanner";
import { fmt, sourceLine, unitLabel, withUnit } from "./format";
import type { HeatmapData } from "./types";

/** A scalar field drawn cell by cell. One pixel of canvas per stored cell, scaled with crisp
 * edges, so what you see is exactly the stored grid (at most 128 by 128, cut by selection on the
 * server). The colour ramp comes from the Consilith tokens, read from the page so it follows the
 * Paper and Stage themes. Values appear on hover or with the arrow keys, and as a table. */

const TABLE_CELLS = 12;
const MAX_HEIGHT = 360;
const TICKS = 5;
// Fallbacks for a document without the stylesheet (tests, print): the Paper tokens.
const FALLBACK: Record<string, Rgb> = {
  "--surface-3": [236, 233, 225],
  "--surface-2": [244, 242, 236],
  "--seat-teal": [13, 128, 137],
  "--seat-violet": [91, 46, 166],
  "--seat-blue": [42, 80, 204],
  "--seat-amber": [180, 83, 15],
};
const NAMES = Object.keys(FALLBACK);

/** The ramp colours, read from the tokens now and again when the theme changes. */
function useTokenColors(ref: React.RefObject<HTMLElement | null>): Record<string, Rgb> {
  const [colors, setColors] = useState<Record<string, Rgb>>(FALLBACK);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof getComputedStyle === "undefined") return;
    const read = () => {
      const style = getComputedStyle(el);
      const next: Record<string, Rgb> = {};
      for (const name of NAMES) {
        next[name] = parseColor(style.getPropertyValue(name)) ?? (FALLBACK[name] as Rgb);
      }
      setColors(next);
    };
    read();
    const observer = typeof MutationObserver === "undefined" ? null : new MutationObserver(read);
    observer?.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const media =
      typeof matchMedia === "undefined" ? null : matchMedia("(prefers-color-scheme: dark)");
    media?.addEventListener("change", read);
    return () => {
      observer?.disconnect();
      media?.removeEventListener("change", read);
    };
  }, [ref]);
  return colors;
}

export function ramp(scale: "sequential" | "diverging", c: Record<string, Rgb>): Rgb[] {
  const pick = (name: string) => c[name] ?? (FALLBACK[name] as Rgb);
  return scale === "diverging"
    ? [pick("--seat-blue"), pick("--surface-2"), pick("--seat-amber")]
    : [pick("--surface-3"), pick("--seat-teal"), pick("--seat-violet")];
}

/** Where a value sits on the ramp, 0..1. A diverging scale is centred on zero. */
export function position(
  v: number,
  min: number,
  max: number,
  scale: "sequential" | "diverging",
): number {
  if (scale === "diverging") {
    const m = Math.max(Math.abs(min), Math.abs(max)) || 1;
    return (v + m) / (2 * m);
  }
  return max > min ? (v - min) / (max - min) : 0.5;
}

export function HeatmapView({ data, title }: { data: HeatmapData; title?: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const uid = useId();
  const tokens = useTokenColors(wrap);
  const rows = data.values;
  const ny = rows.length;
  const nx = rows[0]?.length ?? 0;
  const scale = data.colorScale;
  const stops = useMemo(() => ramp(scale, tokens), [scale, tokens]);
  const [cell, setCell] = useState<{ ix: number; iy: number } | null>(null);

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext?.("2d");
    if (!el || !ctx || !nx || !ny) return;
    el.width = nx;
    el.height = ny;
    const image = ctx.createImageData(nx, ny);
    rows.forEach((row, y) => {
      row.forEach((v, x) => {
        const [r, g, b] = rampAt(stops, position(v, data.min, data.max, scale));
        const o = (y * nx + x) * 4;
        image.data[o] = r;
        image.data[o + 1] = g;
        image.data[o + 2] = b;
        image.data[o + 3] = 255;
      });
    });
    ctx.putImageData(image, 0, 0);
  }, [rows, nx, ny, stops, scale, data.min, data.max]);

  const extent = data.grid.extent;
  const ratio = (() => {
    const physical =
      extent && extent.length === 4
        ? Math.abs(((extent[1] ?? 1) - (extent[0] ?? 0)) / ((extent[3] ?? 1) - (extent[2] ?? 0)))
        : nx / Math.max(1, ny);
    return Number.isFinite(physical) && physical > 0 ? Math.min(2.5, Math.max(0.4, physical)) : 1;
  })();
  const unit = data.unit ? ` ${unitLabel(data.unit)}` : "";
  const axes = useMemo(() => axisTicks(extent, nx, ny, data), [extent, nx, ny, data]);
  const yWidth = Math.max(18, Math.max(1, ...axes.y.map((t) => t.label.length)) * 6.4 + 6);
  const flags = seriesFlags([
    { name: data.label ?? "This field", stamp: data.stamp, status: data.status },
  ]);
  const gradient = `linear-gradient(to right, ${Array.from({ length: 9 }, (_, i) =>
    cssRgb(rampAt(stops, i / 8)),
  ).join(", ")})`;
  const at = (c: { ix: number; iy: number } | null) => (c ? rows[c.iy]?.[c.ix] : undefined);
  const where = (c: { ix: number; iy: number }) => {
    const base = `column ${c.ix + 1}, row ${c.iy + 1}`;
    if (extent?.length !== 4) return base;
    const x = (extent[0] ?? 0) + ((c.ix + 0.5) / nx) * ((extent[1] ?? 0) - (extent[0] ?? 0));
    const y = (extent[2] ?? 0) + ((c.iy + 0.5) / ny) * ((extent[3] ?? 0) - (extent[2] ?? 0));
    return `${base} (x ${fmt(x)}, y ${fmt(y)})`;
  };
  const move = (dx: number, dy: number) =>
    setCell((c) => {
      const cur = c ?? { ix: 0, iy: 0 };
      return {
        ix: Math.max(0, Math.min(nx - 1, cur.ix + dx)),
        iy: Math.max(0, Math.min(ny - 1, cur.iy + dy)),
      };
    });
  const label = `Heatmap of ${data.label ?? "a field"}, ${nx} by ${ny} cells, from ${fmt(
    data.min,
  )} to ${fmt(data.max)}${unit}`;
  const value = at(cell);

  return (
    <figure className={s.heat} aria-label={title ?? data.title ?? data.label ?? "Heatmap"}>
      <figcaption className={s.head}>
        <span className={s.title}>{title ?? data.title ?? data.label ?? "Field"}</span>
        <span className={s.meta}>{data.stamp ? <Stamp kind={data.stamp} /> : null}</span>
      </figcaption>
      <FlagBanner flags={flags} />
      <div className={s.field} ref={wrap}>
        <div
          className={s.frame}
          style={
            {
              "--ar": String(ratio),
              "--ytick": `${yWidth}px`,
              width: `min(100%, ${Math.round(MAX_HEIGHT * ratio + yWidth + 24)}px)`,
            } as CSSProperties
          }
        >
          <span className={s.yTitle} aria-hidden="true">
            {axes.yTitle}
          </span>
          <div className={s.yTicks} aria-hidden="true">
            {axes.y.map((t) => (
              <span key={t.value} style={{ top: `${t.at * 100}%` }}>
                {t.label}
              </span>
            ))}
          </div>
          <div className={s.canvasWrap} style={{ aspectRatio: String(ratio) }}>
            <canvas
              ref={canvas}
              className={s.canvas}
              width={nx}
              height={ny}
              style={{ width: "100%", height: "100%" }}
              tabIndex={0}
              role="img"
              aria-label={label}
              aria-describedby={`${uid}-read`}
              onPointerMove={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                if (!r.width || !r.height) return;
                setCell({
                  ix: Math.max(
                    0,
                    Math.min(nx - 1, Math.floor(((e.clientX - r.left) / r.width) * nx)),
                  ),
                  iy: Math.max(
                    0,
                    Math.min(ny - 1, Math.floor(((e.clientY - r.top) / r.height) * ny)),
                  ),
                });
              }}
              onPointerLeave={() => setCell(null)}
              onBlur={() => setCell(null)}
              onKeyDown={(e) => {
                const step: Record<string, [number, number]> = {
                  ArrowLeft: [-1, 0],
                  ArrowRight: [1, 0],
                  ArrowUp: [0, -1],
                  ArrowDown: [0, 1],
                };
                const d = step[e.key];
                if (d) {
                  e.preventDefault();
                  move(d[0], d[1]);
                } else if (e.key === "Escape") setCell(null);
              }}
            />
            {cell ? (
              <i
                className={s.cell}
                style={{
                  left: `${(cell.ix / nx) * 100}%`,
                  top: `${(cell.iy / ny) * 100}%`,
                  width: `${100 / nx}%`,
                  height: `${100 / ny}%`,
                  minWidth: 6,
                  minHeight: 6,
                }}
              />
            ) : null}
          </div>
          <div className={s.xTicks} aria-hidden="true">
            {axes.x.map((t) => (
              <span key={t.value} style={{ left: `${t.at * 100}%` }}>
                {t.label}
              </span>
            ))}
          </div>
          <span className={s.xTitle} aria-hidden="true">
            {axes.xTitle}
          </span>
        </div>
        <p className={s.readout} id={`${uid}-read`} aria-live="polite">
          {cell && value !== undefined ? (
            <>
              <b>
                {fmt(value)}
                {unit}
              </b>{" "}
              at {where(cell)}
            </>
          ) : (
            "Hover or use the arrow keys to read a cell."
          )}
        </p>
        <div className={s.scale} data-part="scale" aria-hidden="true">
          <div className={s.bar2} style={{ background: gradient }} />
          <div className={s.scaleLabels}>
            <span>{fmt(data.min)}</span>
            {scale === "diverging" ? <em>0</em> : <em>{unit.trim()}</em>}
            <span>
              {fmt(data.max)}
              {scale === "diverging" ? unit : ""}
            </span>
          </div>
        </div>
      </div>
      <footer className={s.foot}>
        {data.decimatedFrom ? (
          <span>
            Showing {nx} by {ny} of the stored {data.decimatedFrom.nx} by {data.decimatedFrom.ny}{" "}
            cells. Values are not averaged.
          </span>
        ) : null}
        {sourceLine(data) ? (
          <span className={s.source} title="where this data comes from">
            {sourceLine(data)}
          </span>
        ) : null}
      </footer>
      <details className={s.table}>
        <summary>Data table</summary>
        <div className={s.scroll}>
          <table>
            <caption>
              {nx > TABLE_CELLS || ny > TABLE_CELLS
                ? `Top left ${Math.min(nx, TABLE_CELLS)} by ${Math.min(ny, TABLE_CELLS)} of ${nx} by ${ny} cells`
                : `${nx} by ${ny} cells`}
              {unit ? `, values in${unit}` : ""}
            </caption>
            <thead>
              <tr>
                <th scope="col">row</th>
                {Array.from({ length: Math.min(nx, TABLE_CELLS) }, (_, x) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: fixed grid coordinate
                  <th scope="col" key={x}>
                    {x + 1}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, TABLE_CELLS).map((row, y) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: fixed grid coordinate
                <tr key={y}>
                  <th scope="row">{y + 1}</th>
                  {row.slice(0, TABLE_CELLS).map((v, x) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: fixed grid coordinate
                    <td key={x}>{fmt(v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

type Tick = { value: number; label: string; at: number };

/** Round tick values along both axes, at their position (0 to 1) across the grid. The x axis
 * runs left to right and the y axis top to bottom, the way the stored rows are ordered, so a
 * tick sits where the hover readout would report that coordinate. Without a stored extent the
 * axes count cells. */
function axisTicks(extent: number[] | null, nx: number, ny: number, data: HeatmapData) {
  const known = !!extent && extent.length === 4;
  const [x0, x1, y0, y1] = known ? (extent as number[]) : [0, nx, 0, ny];
  const along = (lo: number, hi: number): Tick[] => {
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi === lo) return [];
    const step = niceStep((hi - lo) / (TICKS - 1));
    return ticksWithin(Math.min(lo, hi), Math.max(lo, hi), TICKS).map((value) => ({
      value,
      label: tickLabel(value, step),
      at: (value - lo) / (hi - lo),
    }));
  };
  const name = (label: string | undefined, fallback: string, unit: string | undefined) =>
    withUnit(label || fallback, known ? unit : "cells");
  return {
    x: along(x0 ?? 0, x1 ?? 1),
    y: along(y0 ?? 0, y1 ?? 1),
    xTitle: name(data.xLabel, "x", data.xUnit),
    yTitle: name(data.yLabel, "y", data.yUnit),
  };
}
