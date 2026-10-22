import { useState } from "react";
import s from "./a2ui.module.css";
import { Provenance } from "./Provenance";

type Scalar = string | number | boolean | null;

const HEX = /^[0-9a-f]{20,}$/i;
const CLAMP_AT = 90; // characters of text before it is cut to two lines with a "Show more"
const MAX_ROWS = 24;

function isScalar(v: unknown): v is Scalar {
  return v === null || ["string", "number", "boolean"].includes(typeof v);
}

type Compact = Scalar | Scalar[] | [string, Compact][];
const MAX_LIST = 12;

/** An object that reads as a key/value list: up to 24 keys whose values are scalars, short lists
 * of scalars, or (one level down) objects of those. Anything deeper keeps the pretty-printed
 * block, so nothing is hidden. */
function flatEntries(value: unknown, depth = 0): [string, Compact][] | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length || entries.length > MAX_ROWS) return null;
  const out: [string, Compact][] = [];
  for (const [k, v] of entries) {
    if (isScalar(v)) out.push([k, v]);
    else if (Array.isArray(v) && v.length <= MAX_LIST && v.every(isScalar)) out.push([k, v]);
    else if (depth === 0) {
      const inner = flatEntries(v, 1);
      if (!inner) return null;
      out.push([k, inner]);
    } else return null;
  }
  return out;
}

function Clamped({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > CLAMP_AT;
  return (
    <>
      <span className={s.kvText} data-clamped={long && !open ? "true" : undefined}>
        {text}
      </span>
      {long ? (
        <button
          type="button"
          className={s.kvMore}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Show less" : "Show more"}
        </button>
      ) : null}
    </>
  );
}

function KvValue({ name, value }: { name: string; value: Compact }) {
  if (Array.isArray(value) && value.every((v) => Array.isArray(v)))
    return <KeyValueList entries={value as [string, Compact][]} />;
  if (Array.isArray(value)) return <Clamped text={(value as Scalar[]).map(String).join(", ")} />;
  if (typeof value === "string" && HEX.test(value))
    return <Provenance text={value} label={name} className={s.kvHash} />;
  if (typeof value === "string") return <Clamped text={value} />;
  return <span className={s.kvNum}>{String(value)}</span>;
}

/** A small key/value list for a compact JSON object: mono values, a hash cut with a copy button,
 * long text clamped to two lines with a toggle, lists joined, one nested level indented. */
export function KeyValueList({ entries }: { entries: [string, Compact][] }) {
  return (
    <dl className={s.kv}>
      {entries.map(([k, v]) => (
        <div key={k} className={s.kvRow}>
          <dt>{k.replaceAll("_", " ")}</dt>
          <dd>
            <KvValue name={k} value={v} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Large structured results remain inspectable without displacing the working views. */
export function JsonPreview({ value, limit }: { value: unknown; limit?: number }) {
  const flat = flatEntries(value);
  if (flat) return <KeyValueList entries={flat} />;
  const json = JSON.stringify(value, null, 2) ?? "null";
  const truncated = limit !== undefined && json.length > limit;
  const content = (
    <>
      <pre className={s.jsonPreview}>{limit === undefined ? json : json.slice(0, limit)}</pre>
      {truncated ? (
        <p className={s.note}>
          Preview truncated. Download the artifact for its complete contents.
        </p>
      ) : null}
    </>
  );
  return json.length > 2000 ? (
    <details className={s.jsonDetails}>
      <summary>Inspect structured data · {json.length.toLocaleString()} characters</summary>
      {content}
    </details>
  ) : (
    content
  );
}
