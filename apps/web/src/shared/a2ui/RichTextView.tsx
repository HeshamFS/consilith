import b from "./blocks.module.css";
import { fmt, unitLabel } from "./format";
import { proseProblems } from "./proseGuard";
import type { Quantity, RichSegment, RichTextData } from "./types";

/** Short prose with values in it. The agent writes the literals and the server expands each
 * `ref` to a Quantity with its provenance id, so no number in the sentence was typed by a model.
 * A literal that holds a digit, a numeral, a number word or a placeholder is not shown: the
 * browser applies the same prose guard as the room, whatever the server sent. Nothing here is
 * markup: text is rendered as text, with no HTML, images or links. */

export const MAX_SEGMENTS = 60;

function isQuantity(v: unknown): v is Quantity {
  const q = v as Quantity | null;
  return (
    !!q &&
    typeof q === "object" &&
    typeof q.v === "number" &&
    Number.isFinite(q.v) &&
    typeof q.unit === "string" &&
    typeof q.provenanceId === "string" &&
    q.provenanceId !== ""
  );
}

function Piece({ seg }: { seg: RichSegment }) {
  if (seg && typeof seg === "object" && "lit" in seg) {
    const text = typeof seg.lit === "string" ? seg.lit : "";
    const problems = proseProblems(text);
    if (problems.length)
      return (
        <span className={b.withheld} title={`Text withheld: ${problems.join("; ")}`}>
          [text withheld]
        </span>
      );
    return <>{text}</>;
  }
  const ref = seg && typeof seg === "object" && "ref" in seg ? seg.ref : undefined;
  if (isQuantity(ref)) {
    const off = !!ref.status && ref.status !== "current";
    return (
      <span
        className={b.inlineQty}
        data-stamp={ref.stamp ?? "none"}
        data-off={off}
        title={[
          `provenance ${ref.provenanceId}`,
          ref.tier !== undefined ? `tier ${ref.tier}` : "",
          ref.stamp ?? "",
          off ? (ref.status ?? "") : "",
        ]
          .filter(Boolean)
          .join(" · ")}
      >
        <span className={b.inlineNum}>{fmt(ref.v)}</span>
        {ref.unit ? <span className={b.inlineUnit}>{unitLabel(ref.unit)}</span> : null}
        {ref.illustrative ? <span className={b.inlineTag}>illustrative</span> : null}
        {ref.stamp && ref.stamp !== "verified" ? (
          <span className={b.inlineTag} data-stamp={ref.stamp}>
            {ref.stamp}
          </span>
        ) : null}
        {off ? <span className={b.inlineTag}>{ref.status}</span> : null}
      </span>
    );
  }
  const note = (ref as { placeholder?: unknown } | undefined)?.placeholder;
  if (typeof note === "string") return <span className={b.withheld}>{note}</span>;
  return <span className={b.withheld}>[value unavailable]</span>;
}

export function RichTextView({ data, title }: { data: RichTextData; title?: string | undefined }) {
  const segments = Array.isArray(data?.segments) ? data.segments.slice(0, MAX_SEGMENTS) : [];
  if (!segments.length) return <p className={b.empty}>No text yet</p>;
  return (
    <div className={b.rich}>
      {title ? <h3 className={b.richTitle}>{title}</h3> : null}
      <p className={b.richBody}>
        {segments.map((seg, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: segments are positional prose
          <Piece key={i} seg={seg} />
        ))}
      </p>
    </div>
  );
}
