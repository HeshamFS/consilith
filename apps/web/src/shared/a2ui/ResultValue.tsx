import { Stamp, type StampKind } from "@/shared/ui";
import s from "./a2ui.module.css";
import { parsePlotArtifact } from "./artifactData";
import { DataArtifact } from "./DataArtifact";
import { JsonPreview } from "./JsonPreview";

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
      ) : result.kind === "json" && parsePlotArtifact(result.value) ? (
        // a table or chart a tool returned as a value: drawn, never dumped as JSON
        <DataArtifact data={result.value} />
      ) : result.kind === "json" ? (
        <JsonPreview value={result.value} />
      ) : typeof result.value === "string" &&
        (result.value.includes("\n") || result.value.length > 160) ? (
        // a report-like text (aligned columns, many lines): kept as written, in a scroll box
        <pre className={s.resultText}>{result.value}</pre>
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
