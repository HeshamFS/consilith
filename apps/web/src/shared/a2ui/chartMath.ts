/** Scales and ticks for the chart and heatmap. Presentation only: they choose where a stored
 * value is drawn and which round numbers label the axis, and they never produce a value that
 * is shown as data. */

export type Extent = { min: number; max: number };

export function extentOf(values: readonly number[]): Extent | null {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const v of values) {
    if (!Number.isFinite(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return Number.isFinite(min) ? { min, max } : null;
}

/** The step (1, 2 or 5 times a power of ten) closest to `raw` on a log scale, so about as many
 * ticks come out as were asked for, never far fewer. */
export function niceStep(raw: number): number {
  if (!(raw > 0) || !Number.isFinite(raw)) return 1;
  const base = 10 ** Math.floor(Math.log10(raw));
  const f = raw / base;
  const best = [1, 2, 5, 10].reduce((a, c) =>
    Math.abs(Math.log(f / c)) < Math.abs(Math.log(f / a)) ? c : a,
  );
  return best * base;
}

/** Round tick values inside [min, max], about `count` of them. A flat range gets one tick. */
export function ticksWithin(min: number, max: number, count: number): number[] {
  if (!(max > min)) return [min];
  const step = niceStep((max - min) / Math.max(1, count - 1));
  const first = Math.ceil(min / step - 1e-9) * step;
  const out: number[] = [];
  for (let v = first, i = 0; v <= max + step * 1e-9 && i < 50; v += step, i++) {
    out.push(Number.parseFloat(v.toPrecision(12)));
  }
  return out;
}

/** The smallest round-number domain that holds [min, max], so the top gridline is a tick. */
export function niceDomain(min: number, max: number, count: number): Extent {
  if (!(max > min)) {
    const pad = Math.abs(min) > 0 ? Math.abs(min) * 0.1 : 1;
    return { min: min - pad, max: max + pad };
  }
  const step = niceStep((max - min) / Math.max(1, count - 1));
  return {
    min: Number.parseFloat((Math.floor(min / step + 1e-9) * step).toPrecision(12)),
    max: Number.parseFloat((Math.ceil(max / step - 1e-9) * step).toPrecision(12)),
  };
}

/** A tick label with only as many decimals as the tick spacing needs. */
export function tickLabel(v: number, step: number): string {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(1).replace("e+", "e");
  const decimals = Math.min(6, Math.max(0, -Math.floor(Math.log10(step) + 1e-9)));
  return v.toFixed(decimals);
}

export function linear(domain: Extent, range: [number, number]): (v: number) => number {
  const span = domain.max - domain.min || 1;
  return (v) => range[0] + ((v - domain.min) / span) * (range[1] - range[0]);
}

/** Index of the value in `xs` closest to `target`; `xs` need not be sorted. */
export function nearestIndex(xs: readonly number[], target: number): number {
  let best = 0;
  let gap = Number.POSITIVE_INFINITY;
  for (let i = 0; i < xs.length; i++) {
    const d = Math.abs((xs[i] ?? 0) - target);
    if (d < gap) {
      gap = d;
      best = i;
    }
  }
  return best;
}

export type Rgb = [number, number, number];

/** `#rgb`, `#rrggbb` or `rgb(r g b)` to channels; null when it is anything else. */
export function parseColor(text: string): Rgb | null {
  const t = text.trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(t);
  if (hex?.[1]) {
    const h =
      hex[1].length === 3
        ? hex[1]
            .split("")
            .map((c) => c + c)
            .join("")
        : hex[1];
    return [
      Number.parseInt(h.slice(0, 2), 16),
      Number.parseInt(h.slice(2, 4), 16),
      Number.parseInt(h.slice(4, 6), 16),
    ];
  }
  const rgb = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(t);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  return null;
}

/** The colour at `t` (0..1) along evenly spaced stops, mixed in sRGB. */
export function rampAt(stops: readonly Rgb[], t: number): Rgb {
  const first = stops[0] ?? [0, 0, 0];
  if (stops.length < 2) return first;
  const x = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const lo = Math.min(stops.length - 2, Math.floor(x));
  const a = stops[lo] ?? first;
  const b = stops[lo + 1] ?? a;
  const m = x - lo;
  return [
    Math.round(a[0] + (b[0] - a[0]) * m),
    Math.round(a[1] + (b[1] - a[1]) * m),
    Math.round(a[2] + (b[2] - a[2]) * m),
  ];
}

export const cssRgb = (c: Rgb) => `rgb(${c[0]} ${c[1]} ${c[2]})`;

/** At most `cap` indices out of `n`, evenly spaced, first and last kept. Selection only: a
 * long stored series is thinned by picking points, never by averaging or interpolating. */
export function selectIndices(n: number, cap: number): number[] {
  if (n <= cap) return Array.from({ length: n }, (_, i) => i);
  if (cap <= 1) return [0];
  const out = new Set<number>();
  for (let i = 0; i < cap; i++) out.add(Math.round((i * (n - 1)) / (cap - 1)));
  return [...out].sort((a, b) => a - b);
}
