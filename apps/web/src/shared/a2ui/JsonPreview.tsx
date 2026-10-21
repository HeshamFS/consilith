import s from "./a2ui.module.css";

/** Large structured results remain inspectable without displacing the working views. */
export function JsonPreview({ value, limit }: { value: unknown; limit?: number }) {
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
