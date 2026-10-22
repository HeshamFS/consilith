import { type DragEvent, useState } from "react";
import s from "./a2ui.module.css";

/** A compact file field: a dashed zone with one line of text. The browser's own control stays in
 * the DOM (hidden, still focusable and labelled) so keyboard, screen readers and tests reach it. */
export function DropZone({
  id,
  accept,
  disabled,
  busy,
  title,
  hint,
  onFile,
}: {
  id: string;
  accept?: string | undefined;
  disabled: boolean;
  busy: boolean;
  title: string;
  hint: string;
  onFile: (file: File) => void;
}) {
  const [over, setOver] = useState(false);
  const off = disabled || busy;
  const drop = (e: DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file && !off) onFile(file);
  };
  return (
    <label
      className={s.drop}
      aria-disabled={off}
      data-over={over || undefined}
      onDragOver={(e) => {
        e.preventDefault();
        if (!off) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <input
        id={id}
        className={s.inputFile}
        type="file"
        accept={accept}
        disabled={off}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
      <span className={s.dropIcon} aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" role="presentation">
          <title>Upload</title>
          <path
            d="M12 16V5m0 0L7.5 9.5M12 5l4.5 4.5M5 19h14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className={s.dropText}>
        <strong>{busy ? "Storing…" : title}</strong>
        <span>{hint}</span>
      </span>
    </label>
  );
}
