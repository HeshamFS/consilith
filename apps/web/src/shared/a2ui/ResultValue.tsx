import { Stamp, type StampKind } from "@/shared/ui";
import s from "./a2ui.module.css";

export type ResultKind = "number" | "text" | "category" | "json" | "artifact";
export type TypedResult = {
  kind?: ResultKind;
  value?: unknown;
  unit?: string;
  stamp?: StampKind;
  status?: string;
  artifacts?: { sha256: string; mediaType: string; role?: string }[];
};
/** Values are recorded tool outputs. Text/JSON are escaped by React, never interpreted as HTML. */
export function ResultValue({
  result,
  showStamp = true,
}: {
  result: TypedResult;
  showStamp?: boolean;
}) {
  return (
    <div>
      {result.kind === "artifact" ? (
        <span>Artifact output · {result.artifacts?.length ?? 0} files</span>
      ) : result.kind === "json" ? (
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          {JSON.stringify(result.value, null, 2)}
        </pre>
      ) : (
        <span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          {String(result.value ?? "")}
          {result.unit ? ` ${result.unit}` : ""}
        </span>
      )}
      {showStamp && result.stamp ? <Stamp kind={result.stamp} /> : null}
      {result.status && result.status !== "current" ? (
        <span className={s.note}>{result.status}</span>
      ) : null}
    </div>
  );
}
