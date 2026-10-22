/** Presentation-only number and unit formatting. Nothing here changes a value; it only chooses
 * how many digits to show. */

/** Four significant digits, or exponent form for very large and very small magnitudes. */
export function fmt(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  const a = Math.abs(n);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return n.toExponential(2);
  return Number.parseFloat(n.toPrecision(4)).toString();
}

/** A unit as printed: superscripts for powers and the micro sign for `um`. */
export function unitLabel(u: string): string {
  return u
    .replace(/\*\*2/g, "²")
    .replace(/\*\*3/g, "³")
    .replace(/^um/, "µm")
    .replace(/\bum\b/g, "µm");
}

/** `label (unit)` when a unit is known. */
export function withUnit(label: string, unit: string | undefined): string {
  return unit ? `${label} (${unitLabel(unit)})` : label;
}

/** The muted line under a chart or table: where the data came from, in one line. */
export function sourceLine(d: {
  provenanceId?: string | null | undefined;
  tier?: number | undefined;
  ledgerId?: string | null | undefined;
}): string {
  const parts = [
    d.provenanceId ? `Source ${d.provenanceId}` : "",
    d.tier !== undefined ? `Tier ${d.tier}` : "",
    d.ledgerId ?? "",
  ].filter(Boolean);
  return parts.join(" · ");
}
