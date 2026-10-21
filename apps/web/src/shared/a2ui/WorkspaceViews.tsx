import { DataArtifact } from "./DataArtifact";

export { DataArtifact } from "./DataArtifact";

import { useEffect, useState } from "react";
import type { FieldDef } from "@/shared/domain/disciplines";
import { Button, Mark } from "@/shared/ui";
import s from "./a2ui.module.css";
import type { A2UIAction } from "./Renderer";
import { useFieldDraft } from "./useFieldDraft";

export type WorkspaceActions = {
  scope?: string | undefined;
  onAction?: ((action: A2UIAction) => Promise<void>) | undefined;
  loadArtifact?: ((sha: string) => Promise<Blob>) | undefined;
};
type InputField = { definition: FieldDef; value: unknown; path: string; present: boolean };
type InputData = {
  title: string;
  case: string;
  lane: string;
  owner: string | null;
  editable: boolean;
  fields: InputField[];
};

export function WorkspaceView({
  kind,
  value,
  onAction,
  loadArtifact,
  scope,
}: { kind: string; value: unknown } & WorkspaceActions) {
  if (!value || typeof value !== "object")
    return <p className={s.placeholder}>Waiting for bound data</p>;
  const d = value as Record<string, unknown>;
  if (kind === "SchemaForm" || kind === "ParameterGrid")
    return <SchemaForm data={value as InputData} onAction={onAction} scope={scope} />;
  if (kind === "JobStatus") return <Job data={d} onAction={onAction} />;
  if (kind === "ArtifactView")
    return (
      <section className={s.panel}>
        <h3 className={s.panelTitle}>Artifacts</h3>
        {Array.isArray(d.items) && d.items.length ? (
          d.items.map((item, i) => (
            <Artifact key={String(item.sha256 ?? i)} descriptor={item} load={loadArtifact} />
          ))
        ) : (
          <div className={s.artifactEmpty}>
            <Mark size={54} />
            <span className={s.eyebrow}>Model space</span>
            <h4>A place for your model to take shape.</h4>
            <p>
              Add an input artifact, or run when the controls are ready. Computed outputs appear
              here with their source.
            </p>
            <span className={s.emptyBoundary}>Not computed yet</span>
          </div>
        )}
      </section>
    );
  if (kind === "EvidenceView")
    return (
      <section className={s.panel}>
        <h3 className={s.panelTitle}>Evidence</h3>
        <p>{String(d.state ?? "absent")}</p>
        {Array.isArray(d.items)
          ? d.items.map((item) => (
              <div key={String(item.name)} className={s.valueRow}>
                <span>{String(item.name)}</span>
                <span>
                  {String(item.status)} · {String(item.stamp)}
                </span>
                <span className={s.prov}>
                  {String(item.provenanceId)}
                  {item.ledgerId ? ` · ${item.ledgerId}` : ""}
                </span>
              </div>
            ))
          : null}
      </section>
    );
  return null;
}
function SchemaForm({ data, onAction, scope }: { data: InputData } & WorkspaceActions) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  async function edit(field: InputField, value: unknown) {
    if (!onAction || !data.editable) return;
    setBusy(field.path);
    setError(null);
    try {
      await onAction({
        name: "edit",
        path: field.path,
        value,
        present: field.present,
        reason: `${field.definition.label || field.definition.name} updated from workspace`,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Input update failed");
      throw e;
    } finally {
      setBusy(null);
    }
  }
  return (
    <section className={s.panel} aria-label={data.title}>
      <header className={s.panelHead}>
        <h3 className={s.panelTitle}>{data.title}</h3>
        <span className={s.note}>
          {data.owner ?? "unclaimed"}
          {data.editable ? " · editable" : " · read only"}
        </span>
      </header>
      {data.fields.map((field) => (
        <SchemaField
          key={field.path}
          scope={scope}
          field={field}
          disabled={!data.editable || !onAction || busy !== null}
          onCommit={(v) => edit(field, v)}
        />
      ))}
      {error ? (
        <p role="alert" className={s.err}>
          {error}
        </p>
      ) : null}
    </section>
  );
}
function SchemaField({
  field,
  scope,
  disabled,
  onCommit,
}: {
  field: InputField;
  scope?: string | undefined;
  disabled: boolean;
  onCommit: (v: unknown) => Promise<void>;
}) {
  const f = field.definition;
  const display =
    f.kind === "object" || f.kind === "list"
      ? JSON.stringify(field.value ?? (f.kind === "list" ? [] : {}), null, 2)
      : String(
          typeof field.value === "object" && field.value !== null && "v" in field.value
            ? (field.value as { v: unknown }).v
            : (field.value ?? ""),
        );
  const {
    text: draft,
    change: setDraft,
    conflict,
    dirty,
    baseValue,
    committed,
    reset,
  } = useFieldDraft(`${scope ?? "isolated"}:${field.path}`, field.value, display);
  const [error, setError] = useState<string | null>(null);
  const id = `field-${field.path}`;
  const numeric = f.kind === "number" || f.kind === "integer";
  const unit =
    numeric && baseValue && typeof baseValue === "object" && "unit" in baseValue
      ? String(baseValue.unit ?? "")
      : (f.unit ?? "");
  const schemaBoundsApply = unit === (f.unit ?? "");
  async function commit(force = false, selected?: string) {
    if (disabled || (conflict && !force)) return;
    if (!dirty && selected === undefined && f.kind !== "list" && f.kind !== "object") return;
    setError(null);
    try {
      const text = selected ?? draft;
      let v: unknown = text;
      if (f.kind === "number" || f.kind === "integer") {
        if (!text.trim() || !Number.isFinite(Number(text)))
          throw new Error("Enter a finite number");
        const n = Number(text);
        if (f.kind === "integer" && !Number.isInteger(n)) throw new Error("Enter an integer");
        if (schemaBoundsApply && ((f.min != null && n < f.min) || (f.max != null && n > f.max)))
          throw new Error("Value is outside the declared bounds");
        v = { v: n, unit };
      } else if (f.kind === "list" || f.kind === "object") {
        v = JSON.parse(text);
        if (f.kind === "list" && !Array.isArray(v)) throw new Error("Expected a JSON array");
        if (f.kind === "object" && (!v || typeof v !== "object" || Array.isArray(v)))
          throw new Error("Expected a JSON object");
      }
      if (f.kind === "choice" && !(f.choices ?? []).includes(String(v)))
        throw new Error("Choose one of the declared options");
      if (JSON.stringify(v) !== JSON.stringify(field.value)) await onCommit(v);
      committed();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid value");
    }
  }
  return (
    <div className={s.inputField}>
      <label htmlFor={id}>
        {f.label || f.name}
        {unit ? ` (${unit})` : ""}
        {f.required ? " *" : ""}
      </label>
      {f.kind === "choice" ? (
        <select
          id={id}
          value={draft}
          disabled={disabled}
          onChange={(e) => {
            setDraft(e.target.value);
            void commit(false, e.target.value);
          }}
        >
          <option value="" disabled={!f.choices?.includes("")}>
            Not set
          </option>
          {(f.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      ) : f.kind === "list" || f.kind === "object" ? (
        <>
          <textarea
            id={id}
            rows={4}
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="button" disabled={disabled} onClick={() => void commit()}>
            Apply {f.label || f.name}
          </button>
        </>
      ) : (
        <input
          id={id}
          type={f.kind === "text" ? "text" : "number"}
          value={draft}
          min={schemaBoundsApply ? (f.min ?? undefined) : undefined}
          max={schemaBoundsApply ? (f.max ?? undefined) : undefined}
          step={f.kind === "integer" ? 1 : "any"}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => void commit()}
          onKeyDown={(e) => {
            if (e.key === "Enter") void commit();
          }}
        />
      )}
      {conflict ? (
        <div className={s.conflict} role="status">
          This input changed elsewhere. Your draft is kept.{" "}
          <Button variant="ghost" small onClick={() => void commit(true)}>
            Apply my draft
          </Button>
          <Button variant="ghost" small onClick={reset}>
            Use shared value
          </Button>
        </div>
      ) : null}
      {error ? (
        <span role="alert" className={s.err}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
function Job({ data, onAction }: { data: Record<string, unknown> } & WorkspaceActions) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    if (!onAction) return;
    setBusy(true);
    setError(null);
    try {
      await onAction({ name: "run", capability: String(data.case) });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Execution failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={s.panel}>
      <h3 className={s.panelTitle}>{String(data.title)}</h3>
      <p>{String(data.description ?? "")}</p>
      <p role="status">Execution: {String(data.state ?? "idle")}</p>
      <Button
        type="button"
        disabled={!data.enabled || !onAction || busy}
        onClick={() => void run()}
      >
        {busy ? "Starting…" : "Run"}
      </Button>
      {!data.enabled ? (
        <span className={s.note}>
          Execution requires all lanes to have an owner and no active job.
        </span>
      ) : null}
      {error || data.error ? (
        <p role="alert" className={s.err}>
          {error ?? String(data.error)}
        </p>
      ) : null}
    </section>
  );
}
type ArtifactDescriptor = {
  label?: string;
  sha256?: string;
  source?: string;
  illustrative?: boolean;
  provenanceId?: string;
  kind?: string;
  data?: unknown;
};
function Artifact({
  descriptor,
  load,
}: {
  descriptor: ArtifactDescriptor;
  load: WorkspaceActions["loadArtifact"];
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [download, setDownload] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [data, setData] = useState<unknown>(descriptor.data);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let objectUrl: string | undefined;
    setUrl(null);
    setDownload(null);
    setText(null);
    setData(descriptor.data);
    setError(null);
    if (!descriptor.sha256 || !load) return;
    void load(descriptor.sha256)
      .then(async (blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setDownload(objectUrl);
        if (blob.type.startsWith("image/")) {
          setUrl(objectUrl);
        } else if (blob.type.includes("json")) {
          const parsed: unknown = JSON.parse(await blob.text());
          if (active) setData(parsed);
        } else if (blob.type.startsWith("text/")) {
          const content = await blob.text();
          if (active) setText(content);
        }
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "Artifact unavailable");
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [descriptor.sha256, descriptor.data, load]);
  return (
    <figure className={s.artifact}>
      <figcaption>
        {descriptor.label ?? "Artifact"} · {descriptor.source ?? "stored"}
        {descriptor.illustrative ? " · illustrative" : ""}
      </figcaption>
      {url ? (
        <img src={url} alt={descriptor.label ?? "Stored artifact"} />
      ) : text !== null ? (
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{text}</pre>
      ) : data !== undefined ? (
        <DataArtifact data={data} />
      ) : null}
      {download ? (
        <a
          href={download}
          download={(descriptor.label ?? "artifact").replace(/[^a-zA-Z0-9._-]/g, "_")}
        >
          Download artifact
        </a>
      ) : null}
      {error ? <p role="status">{error}</p> : null}
      <small>
        {descriptor.sha256}
        {descriptor.provenanceId ? ` · source ${descriptor.provenanceId}` : ""}
      </small>
    </figure>
  );
}
