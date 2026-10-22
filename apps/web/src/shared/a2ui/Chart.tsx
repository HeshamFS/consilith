import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Stamp, type StampKind } from "@/shared/ui";
import s from "./chart.module.css";
import {
  extentOf,
  linear,
  nearestIndex,
  niceDomain,
  niceStep,
  tickLabel,
  ticksWithin,
} from "./chartMath";
import { FlagBanner, seriesFlags } from "./FlagBanner";
import { fmt, sourceLine, unitLabel, withUnit } from "./format";
import type { ChartData, ChartSeries } from "./types";

/** A line, scatter or bar chart of stored series. Every point is drawn from the data model as
 * given: the server picked the columns and cut long series by selection, and this component only
 * decides where a value goes on screen. It labels both axes with units, says which stamp, tier and
 * ledger entry the data carries, warns when a series is not current or not verified, and offers
 * the same numbers as a table for anyone who cannot use the picture. */

const DEFAULT_WIDTH = 440;
const COLORS = 6; // seat tokens; later series differ by dash and marker, never by colour alone
const TABLE_ROWS = 25;
const SEVERITY: StampKind[] = ["blocked", "warning", "checking"];

export { seriesFlags };

/** Series with a stable, unique key: two series may share a name, so repeats get a suffix. */
function keyedSeries(series: readonly ChartSeries[]) {
  const seen = new Map<string, number>();
  return series.map((x, si) => {
    const n = (seen.get(x.name) ?? 0) + 1;
    seen.set(x.name, n);
    return { x, si, key: n > 1 ? `${x.name}#${n}` : x.name };
  });
}

function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setWidth(Math.round(el.getBoundingClientRect().width));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

function Marker({ shape, x, y, r }: { shape: number; x: number; y: number; r: number }) {
  switch (shape % 5) {
    case 1:
      return <rect x={x - r} y={y - r} width={2 * r} height={2 * r} rx={1} />;
    case 2:
      return (
        <path
          d={`M${x} ${y - r * 1.25} L${x + r * 1.25} ${y} L${x} ${y + r * 1.25} L${x - r * 1.25} ${y}Z`}
        />
      );
    case 3:
      return (
        <path d={`M${x} ${y - r * 1.2} L${x + r * 1.15} ${y + r} L${x - r * 1.15} ${y + r}Z`} />
      );
    case 4:
      return (
        <path
          d={`M${x - r} ${y - r} L${x + r} ${y + r} M${x + r} ${y - r} L${x - r} ${y + r}`}
          className={s.cross}
        />
      );
    default:
      return <circle cx={x} cy={y} r={r} />;
  }
}

function seriesUnit(series: ChartSeries, fallback: string): string {
  return series.unit ?? fallback;
}

export function ChartView({ data, title }: { data: ChartData; title?: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const measured = useWidth(wrap);
  const width = Math.max(240, measured || DEFAULT_WIDTH);
  const uid = useId();
  const series = useMemo(() => data.series.filter((x) => x.x.length > 0 && x.y.length > 0), [data]);
  const kind = data.kind;
  const bars = kind === "bar";
  const [stop, setStop] = useState<number | null>(null);

  const geo = useMemo(() => {
    const height = Math.round(Math.min(330, Math.max(222, width * 0.6)));
    const ys = series.flatMap((x) => x.y);
    const yExt = extentOf(bars ? [...ys, 0] : ys) ?? { min: 0, max: 1 };
    const yDom = niceDomain(yExt.min, yExt.max, 5);
    const yTicks = ticksWithin(yDom.min, yDom.max, 5);
    const yStep = niceStep((yDom.max - yDom.min) / 4);
    const yLabels = yTicks.map((t) => tickLabel(t, yStep));
    const left = Math.max(38, Math.max(...yLabels.map((l) => l.length), 1) * 6.6 + 14);
    const right = 14;
    const top = 10;
    // The category or tick labels take one line, the axis title its own line under them.
    const bottom = data.xLabel ? 44 : 30;
    const inner = { w: width - left - right, h: height - top - bottom };
    const cats: string[] = [];
    if (bars) {
      for (const x of series)
        for (const v of x.x) if (!cats.includes(String(v))) cats.push(String(v));
    }
    const xs = series.flatMap((x) => x.x.filter((v): v is number => typeof v === "number"));
    const xExt = extentOf(xs) ?? { min: 0, max: 1 };
    const xDom = xExt.max > xExt.min ? xExt : { min: xExt.min - 1, max: xExt.max + 1 };
    const xTicks = bars
      ? []
      : ticksWithin(xDom.min, xDom.max, Math.max(2, Math.floor(inner.w / 78)));
    const xStep = niceStep((xDom.max - xDom.min) / Math.max(1, xTicks.length - 1 || 4));
    return {
      height,
      yDom,
      yTicks,
      yLabels,
      left,
      right,
      top,
      bottom,
      inner,
      cats,
      xDom,
      xTicks,
      xStep,
    };
  }, [series, width, bars, data.xLabel]);

  const px = bars
    ? (i: number) => geo.left + ((i + 0.5) / Math.max(1, geo.cats.length)) * geo.inner.w
    : linear(geo.xDom, [geo.left, geo.left + geo.inner.w]);
  const py = linear(geo.yDom, [geo.top + geo.inner.h, geo.top]);
  const flags = seriesFlags(series);
  const worst: StampKind =
    SEVERITY.find((k) => series.some((x) => x.stamp === k)) ??
    (series.every((x) => x.stamp === "verified") ? "verified" : "checking");

  // The positions the pointer and the arrow keys step through.
  const stops = useMemo(() => {
    if (bars) return geo.cats.map((_, i) => i);
    const first = series[0]?.x.filter((v): v is number => typeof v === "number") ?? [];
    return [...first].sort((a, b) => a - b);
  }, [bars, geo.cats, series]);
  const setFromPointer = (clientX: number, rect: DOMRect) => {
    const x = clientX - rect.left;
    if (!stops.length) return;
    if (bars) {
      const band = geo.inner.w / Math.max(1, geo.cats.length);
      setStop(Math.max(0, Math.min(stops.length - 1, Math.floor((x - geo.left) / band))));
    } else {
      const value =
        geo.xDom.min + ((x - geo.left) / (geo.inner.w || 1)) * (geo.xDom.max - geo.xDom.min);
      setStop(nearestIndex(stops, value));
    }
  };
  const at = stop === null ? null : (stops[stop] ?? null);
  const keyed = useMemo(() => keyedSeries(series), [series]);
  const readout = useMemo(() => {
    if (at === null) return null;
    const rows = series.flatMap((x, si) => {
      if (bars) {
        const label = geo.cats[at];
        const i = x.x.findIndex((v) => String(v) === label);
        return i < 0 ? [] : [{ si, name: x.name, x: label ?? "", y: x.y[i] ?? 0, series: x }];
      }
      const nums = x.x.map((v) => (typeof v === "number" ? v : Number.NaN));
      const i = nearestIndex(nums, at);
      return [{ si, name: x.name, x: fmt(nums[i] ?? 0), y: x.y[i] ?? 0, series: x }];
    });
    return { head: bars ? String(geo.cats[at] ?? "") : fmt(at), rows };
  }, [at, series, bars, geo.cats]);

  const label = `${bars ? "Bar" : kind === "scatter" ? "Scatter" : "Line"} chart of ${withUnit(
    data.yLabel || "value",
    data.yUnit,
  )} against ${withUnit(data.xLabel || "x", data.xUnit)}, ${series.length} series`;
  const single = series.length === 1 && kind === "line";
  const plotBottom = geo.top + geo.inner.h;
  const bandW = geo.inner.w / Math.max(1, geo.cats.length);
  const groupW = Math.min(bandW * 0.78, 64 * series.length);
  const barW = groupW / Math.max(1, series.length);
  const tipX = readout && at !== null ? px(at) : null;
  const tip = tipX === null ? null : { x: tipX, flip: tipX > geo.left + geo.inner.w * 0.58 };

  return (
    <figure className={s.chart} data-kind={kind} aria-label={title ?? data.title ?? "Chart"}>
      <figcaption className={s.head}>
        <span className={s.title}>{title ?? data.title ?? withUnit(data.yLabel, data.yUnit)}</span>
        <span className={s.meta}>{data.stamp ? <Stamp kind={worst} /> : null}</span>
      </figcaption>
      <FlagBanner flags={flags} />
      <div className={s.axisTitle}>{withUnit(data.yLabel || "value", data.yUnit)}</div>
      <div className={s.plot} ref={wrap}>
        <svg
          width={width}
          height={geo.height}
          viewBox={`0 0 ${width} ${geo.height}`}
          role="img"
          aria-label={label}
          aria-describedby={`${uid}-read`}
          // biome-ignore lint/a11y/noNoninteractiveTabindex: the chart is read with the arrow keys
          tabIndex={0}
          onPointerMove={(e) => setFromPointer(e.clientX, e.currentTarget.getBoundingClientRect())}
          onPointerLeave={() => setStop(null)}
          onBlur={() => setStop(null)}
          onKeyDown={(e) => {
            if (!stops.length) return;
            const last = stops.length - 1;
            const next =
              e.key === "ArrowRight"
                ? Math.min(last, (stop ?? -1) + 1)
                : e.key === "ArrowLeft"
                  ? Math.max(0, (stop ?? last + 1) - 1)
                  : e.key === "Home"
                    ? 0
                    : e.key === "End"
                      ? last
                      : null;
            if (next !== null) {
              e.preventDefault();
              setStop(next);
            } else if (e.key === "Escape") setStop(null);
          }}
        >
          <title>{label}</title>
          {geo.yTicks.map((t, i) => (
            <g key={t}>
              <line
                className={t === 0 && !bars ? s.zero : s.grid}
                x1={geo.left}
                x2={geo.left + geo.inner.w}
                y1={py(t)}
                y2={py(t)}
              />
              <text className={s.tick} x={geo.left - 8} y={py(t)} textAnchor="end" dy="0.32em">
                {geo.yLabels[i]}
              </text>
            </g>
          ))}
          {bars
            ? geo.cats.map((c, i) => (
                <text key={c} className={s.tick} x={px(i)} y={plotBottom + 17} textAnchor="middle">
                  {c.length > Math.max(6, Math.floor(bandW / 6.5))
                    ? `${c.slice(0, Math.max(5, Math.floor(bandW / 6.5) - 1))}…`
                    : c}
                </text>
              ))
            : geo.xTicks.map((t) => (
                <g key={t}>
                  <line
                    className={s.tickMark}
                    x1={px(t)}
                    x2={px(t)}
                    y1={plotBottom}
                    y2={plotBottom + 4}
                  />
                  <text className={s.tick} x={px(t)} y={plotBottom + 17} textAnchor="middle">
                    {tickLabel(t, geo.xStep)}
                  </text>
                </g>
              ))}
          <line
            className={s.axis}
            x1={geo.left}
            x2={geo.left + geo.inner.w}
            y1={plotBottom}
            y2={plotBottom}
          />
          {keyed.map(({ x, si, key }) => {
            const color = si % COLORS;
            const notCurrent = !!x.status && x.status !== "current";
            const dash = si >= COLORS || notCurrent;
            const pts = x.x.map((v, i) => ({
              cx: bars ? 0 : px(typeof v === "number" ? v : 0),
              cy: py(x.y[i] ?? 0),
            }));
            if (bars) {
              return (
                <g key={key} className={s.series} data-c={color} data-off={notCurrent}>
                  {x.x.map((v, i) => {
                    const ci = geo.cats.indexOf(String(v));
                    const cx = px(ci) - groupW / 2 + si * barW;
                    const y0 = py(0);
                    const y1 = py(x.y[i] ?? 0);
                    return (
                      <rect
                        key={String(v)}
                        className={s.bar}
                        data-dash={dash}
                        x={cx + 1}
                        y={Math.min(y0, y1)}
                        width={Math.max(2, barW - 2)}
                        height={Math.max(1, Math.abs(y1 - y0))}
                        rx={3}
                      />
                    );
                  })}
                </g>
              );
            }
            const line = pts.map((p, i) => `${i ? "L" : "M"}${p.cx.toFixed(1)} ${p.cy.toFixed(1)}`);
            return (
              <g key={key} className={s.series} data-c={color} data-off={notCurrent}>
                {single && pts.length > 1 ? (
                  <path
                    className={s.area}
                    d={`${line.join("")}L${pts[pts.length - 1]?.cx.toFixed(1)} ${plotBottom}L${pts[0]?.cx.toFixed(1)} ${plotBottom}Z`}
                  />
                ) : null}
                {kind === "line" ? (
                  <path
                    className={s.line}
                    data-dash={dash}
                    d={line.join("")}
                    pathLength={dash ? undefined : 1}
                  />
                ) : null}
                {kind === "scatter" || pts.length <= 40 ? (
                  <g className={s.dots} data-hollow={notCurrent}>
                    {pts.map((p, i) => (
                      <Marker
                        // biome-ignore lint/suspicious/noArrayIndexKey: points have no id; order is fixed
                        key={i}
                        shape={si}
                        x={p.cx}
                        y={p.cy}
                        r={kind === "scatter" ? 3.6 : 2.6}
                      />
                    ))}
                  </g>
                ) : null}
              </g>
            );
          })}
          {tip && readout ? (
            <g className={s.cursor} pointerEvents="none">
              <line x1={tip.x} x2={tip.x} y1={geo.top} y2={plotBottom} />
              {bars
                ? null
                : readout.rows.map((r) => (
                    <circle
                      key={r.si}
                      className={s.hit}
                      data-c={r.si % COLORS}
                      cx={tip.x}
                      cy={py(r.y)}
                      r={4.5}
                    />
                  ))}
            </g>
          ) : null}
          {data.xLabel ? (
            <text
              className={s.axisLabel}
              x={geo.left + geo.inner.w / 2}
              y={geo.height - 8}
              textAnchor="middle"
            >
              {withUnit(data.xLabel, data.xUnit)}
            </text>
          ) : null}
        </svg>
        {tip && readout ? (
          <div
            className={s.tip}
            data-flip={tip.flip}
            style={{ left: tip.x, top: geo.top + 6 }}
            aria-hidden="true"
          >
            <div className={s.tipHead}>
              {readout.head}
              {!bars && data.xUnit ? ` ${unitLabel(data.xUnit)}` : ""}
            </div>
            {readout.rows.map((r) => (
              <div className={s.tipRow} key={r.si} data-c={r.si % COLORS}>
                <i className={s.swatch} />
                <span className={s.tipName}>{r.name}</span>
                <b className={s.tipVal}>
                  {fmt(r.y)}
                  {seriesUnit(r.series, data.yUnit)
                    ? ` ${unitLabel(seriesUnit(r.series, data.yUnit))}`
                    : ""}
                </b>
              </div>
            ))}
          </div>
        ) : null}
        <p className="visually-hidden" id={`${uid}-read`} aria-live="polite">
          {readout
            ? `${data.xLabel || "x"} ${readout.head}: ${readout.rows
                .map((r) => `${r.name} ${fmt(r.y)} ${seriesUnit(r.series, data.yUnit)}`)
                .join("; ")}`
            : "Use the arrow keys to read values."}
        </p>
      </div>
      {series.length > 1 || flags.length ? (
        <ul className={s.legend} aria-label="Series">
          {keyed.map(({ x, si, key }) => {
            const notCurrent = !!x.status && x.status !== "current";
            return (
              <li key={key} data-c={si % COLORS} data-off={notCurrent}>
                {kind === "bar" ? (
                  <i className={s.square} data-off={notCurrent} aria-hidden="true" />
                ) : (
                  <svg width="22" height="10" aria-hidden="true" className={s.key}>
                    <line
                      x1="1"
                      x2="21"
                      y1="5"
                      y2="5"
                      strokeDasharray={si >= COLORS || notCurrent ? "4 3" : undefined}
                    />
                    <g className={s.keyDot}>
                      <Marker shape={si} x={11} y={5} r={2.8} />
                    </g>
                  </svg>
                )}
                <span>{x.name}</span>
                {x.stamp && x.stamp !== "verified" ? (
                  <span className={s.tag} data-tone={x.stamp}>
                    {x.stamp}
                  </span>
                ) : null}
                {notCurrent ? <span className={s.tag}>{x.status}</span> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      <footer className={s.foot}>
        {series.some((x) => x.decimatedFrom) ? (
          <span>
            Showing a selection of the stored points (
            {series
              .filter((x) => x.decimatedFrom)
              .map((x) => `${x.name}: ${x.x.length} of ${x.decimatedFrom}`)
              .join("; ")}
            ). Values are not averaged.
          </span>
        ) : null}
        {data.omittedSeries ? <span>{data.omittedSeries} more series not shown.</span> : null}
        {sourceLine(data) ? (
          <span className={s.source} title="where this data comes from">
            {sourceLine(data)}
          </span>
        ) : null}
      </footer>
      <details className={s.table}>
        <summary>Data table</summary>
        <DataTable data={data} series={series} />
      </details>
    </figure>
  );
}

function DataTable({ data, series }: { data: ChartData; series: readonly ChartSeries[] }) {
  if (series.every((x) => x.x.length <= 1)) {
    return (
      <div className={s.scroll}>
        <table>
          <thead>
            <tr>
              <th scope="col">{data.xLabel || "Result"}</th>
              <th scope="col">{withUnit(data.yLabel || "Value", data.yUnit)}</th>
              <th scope="col">Stamp</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {series.map((x, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: series can repeat a name
              <tr key={`${x.name}-${i}`}>
                <th scope="row">{x.name}</th>
                <td>
                  {fmt(x.y[0] ?? 0)} {unitLabel(seriesUnit(x, data.yUnit))}
                </td>
                <td>{x.stamp ?? ""}</td>
                <td>{x.status ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <>
      {keyedSeries(series).map(({ x, key }) => (
        <div className={s.scroll} key={key}>
          <table>
            <caption>
              {x.name}
              {x.x.length > TABLE_ROWS ? ` (first ${TABLE_ROWS} of ${x.x.length} points)` : ""}
            </caption>
            <thead>
              <tr>
                <th scope="col">{withUnit(data.xLabel || "x", data.xUnit)}</th>
                <th scope="col">{withUnit(data.yLabel || "y", seriesUnit(x, data.yUnit))}</th>
              </tr>
            </thead>
            <tbody>
              {x.x.slice(0, TABLE_ROWS).map((v, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
                <tr key={i}>
                  <td>{typeof v === "number" ? fmt(v) : v}</td>
                  <td>{fmt(x.y[i] ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}
