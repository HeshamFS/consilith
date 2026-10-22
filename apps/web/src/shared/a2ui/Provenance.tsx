import { useState } from "react";
import s from "./provenance.module.css";

/** Where a number or file came from, as a muted mono line: a long hash is cut to its head and
 * tail, the whole line is one row that truncates, and a small button copies the full value. */

const LONG_HEX = /\b[0-9a-f]{20,}\b/gi;

/** `3f9a1c07d2…e41b` for any run of 20 or more hex digits; other text is kept as it is. */
export function shorten(text: string): string {
  return text.replace(LONG_HEX, (h) => `${h.slice(0, 8)}…${h.slice(-4)}`);
}

export function Provenance({
  text,
  copy,
  label,
  className,
}: {
  /** The line to show; long hashes in it are shortened. */
  text: string;
  /** What the button copies. Absent: the text as given. */
  copy?: string;
  /** Names the copied thing for assistive technology, such as "sha256". */
  label?: string;
  className?: string | undefined;
}) {
  const [copied, setCopied] = useState(false);
  const full = copy ?? text;
  async function doCopy() {
    try {
      await navigator.clipboard.writeText(full);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  }
  return (
    <span className={`${s.prov} ${className ?? ""}`.trim()} data-provenance>
      <span className={s.text} title={full}>
        {shorten(text)}
      </span>
      <button
        type="button"
        className={s.copy}
        aria-label={`Copy ${label ?? "provenance"}`}
        data-copied={copied || undefined}
        onClick={() => void doCopy()}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden="true">
          {copied ? (
            <path
              d="M5 12.5l4.5 4.5L19 7.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : (
            <g fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round">
              <rect x="8.5" y="8.5" width="11" height="11" rx="2.5" />
              <path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" />
            </g>
          )}
        </svg>
        {copied ? <span className="visually-hidden">Copied</span> : null}
      </button>
    </span>
  );
}
