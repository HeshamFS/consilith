/** Generic formats: bounded label map, numeric XY series, table, or structural inspection. */
export function DataArtifact({ data }: { data: unknown }) {
  const d = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  if (
    Array.isArray(d.points) &&
    Array.isArray(d.triangles) &&
    Array.isArray(d.values) &&
    d.points.length <= 50000 &&
    d.triangles.length <= 20000 &&
    d.values.length === d.triangles.length
  ) {
    const points = d.points,
      triangles = d.triangles,
      values = d.values;
    if (
      points.every(
        (p) =>
          Array.isArray(p) &&
          p.length >= 2 &&
          p.slice(0, 2).every((v) => typeof v === "number" && Number.isFinite(v)),
      ) &&
      triangles.every(
        (t) =>
          Array.isArray(t) &&
          t.length === 3 &&
          t.every((i) => Number.isInteger(i) && i >= 0 && i < points.length),
      ) &&
      values.every((v) => typeof v === "number" && Number.isFinite(v))
    ) {
      const xy = points as number[][],
        cells = triangles as number[][],
        scalars = values as number[];
      const xs = xy.map((p) => p[0] ?? 0),
        ys = xy.map((p) => p[1] ?? 0),
        xmin = Math.min(...xs),
        ymin = Math.min(...ys),
        width = Math.max(...xs) - xmin || 1,
        height = Math.max(...ys) - ymin || 1,
        vmin = Math.min(...scalars),
        vmax = Math.max(...scalars),
        span = vmax - vmin || 1;
      return (
        <div>
          <svg
            viewBox={`${xmin} ${ymin} ${width} ${height}`}
            role="img"
            aria-label="Stored scalar mesh"
          >
            {cells.map((cell, i) => (
              <polygon
                key={cell.join("-")}
                points={cell.map((j) => xy[j]?.slice(0, 2).join(",")).join(" ")}
                fill={scalarColor(
                  ((scalars[i] ?? vmin) - vmin) / span,
                  d.colorScale === "diverging",
                )}
              />
            ))}
          </svg>
          <p>
            Range: {vmin} to {vmax} {String(d.unit ?? "")}
          </p>
        </div>
      );
    }
  }
  if (
    Array.isArray(d.labels) &&
    d.labels.length &&
    d.labels.length <= 256 &&
    d.labels.every(
      (r) =>
        Array.isArray(r) &&
        r.length <= 256 &&
        r.every((v) => typeof v === "number" && Number.isFinite(v)),
    )
  ) {
    const rows = d.labels as number[][];
    const width = Math.max(...rows.map((r) => r.length));
    return (
      <svg viewBox={`0 0 ${width} ${rows.length}`} role="img" aria-label="Stored label map">
        {rows.flatMap((row, y) =>
          row.map((label, x) => (
            <rect
              // biome-ignore lint/suspicious/noArrayIndexKey: fixed grid coordinates identify cells
              key={`${x}-${y}`}
              x={x}
              y={y}
              width={1}
              height={1}
              fill={`hsl(${(label * 137.508) % 360} 55% 60%)`}
            />
          )),
        )}
      </svg>
    );
  }
  if (
    Array.isArray(d.x) &&
    Array.isArray(d.y) &&
    d.x.length === d.y.length &&
    d.x.length > 1 &&
    d.x.length <= 20000 &&
    [...d.x, ...d.y].every((v) => typeof v === "number" && Number.isFinite(v))
  ) {
    const x = d.x as number[],
      y = d.y as number[];
    const xmin = Math.min(...x),
      xrange = Math.max(...x) - xmin || 1,
      ymin = Math.min(...y),
      yrange = Math.max(...y) - ymin || 1;
    return (
      <svg viewBox="0 0 640 260" role="img" aria-label="Stored XY series">
        <polyline
          points={x
            .map(
              (v, i) =>
                `${20 + ((v - xmin) / xrange) * 600},${240 - (((y[i] ?? ymin) - ymin) / yrange) * 220}`,
            )
            .join(" ")}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        />
      </svg>
    );
  }
  if (Array.isArray(d.rows) && d.rows.length <= 1000 && Array.isArray(d.columns)) {
    return (
      <table>
        <thead>
          <tr>
            {d.columns.map((c, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: immutable artifact column position
              <th key={`${i}-${c}`}>{String(c)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {d.rows.map((row, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: immutable artifact row position
            <tr key={`row-${i}`}>
              {Array.isArray(row)
                ? row.map((v, j) => (
                    // biome-ignore lint/suspicious/noArrayIndexKey: immutable table cell coordinates
                    <td key={`${i}-${j}`}>{String(v)}</td>
                  ))
                : null}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return <pre>{JSON.stringify(data, null, 2).slice(0, 20000)}</pre>;
}

/** Presentation-only interpolation of supplied scalar values, never scientific inference. */
function scalarColor(fraction: number, diverging: boolean) {
  const stops = diverging
    ? [
        [33, 102, 172],
        [247, 247, 247],
        [178, 24, 43],
      ]
    : [
        [68, 1, 84],
        [59, 82, 139],
        [33, 145, 140],
        [94, 201, 98],
        [253, 231, 37],
      ];
  const t = Math.max(0, Math.min(1, fraction)) * (stops.length - 1);
  const lo = Math.min(stops.length - 2, Math.floor(t)),
    mix = t - lo;
  const a = stops[lo] ?? [0, 0, 0],
    b = stops[lo + 1] ?? a;
  return `rgb(${a.map((v, i) => Math.round(v + ((b[i] ?? v) - v) * mix)).join(" ")})`;
}
