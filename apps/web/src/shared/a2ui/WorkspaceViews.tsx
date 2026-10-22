import { AppFrame, requestAppLink } from "./AppFrame";
import { ArtifactReport, isAppRole, isReport, prettyLabel, useClassified } from "./ArtifactReport";
import { ArtifactSequence } from "./ArtifactSequence";
import plotStyle from "./artifact-data.module.css";
import { jsonLines, parsePlotArtifact } from "./artifactData";
import { DataArtifact } from "./DataArtifact";
import { parseImageSequence } from "./imageSequence";

export { DataArtifact } from "./DataArtifact";

import { type ReactNode, useEffect, useRef, useState } from "react";
import type { Capability, FieldDef } from "@/shared/domain/disciplines";
import { Button, Mark, Stamp, type StampKind } from "@/shared/ui";
import s from "./a2ui.module.css";
import { intakeMediaType } from "./artifactIntake";
import b from "./blocks.module.css";
import { DropZone } from "./DropZone";
import { artifactSha, displayOf, parseDraft, pickedChoices } from "./fieldValue";
import { type ImageActivity, ImageStage } from "./ImageStage";
import { Provenance } from "./Provenance";
import type { A2UIAction } from "./Renderer";
import { RunList } from "./RunList";
import type { RunLive } from "./types";
import { useFieldDraft } from "./useFieldDraft";

export type WorkspaceActions = {
  imageActivity?: ImageActivity | undefined;
  scope?: string | undefined;
  /** The heading a plan gave this view; the view's own default when absent. */
  title?: string | undefined;
  onAction?: ((action: A2UIAction) => Promise<void>) | undefined;
  loadArtifact?: ((sha: string) => Promise<Blob>) | undefined;
  /** The address of a plugin app's page; the room's app route when absent. */
  appLink?: ((sha: string) => Promise<string>) | undefined;
  /** Stores a picked file and returns its sha256: how an `artifact` field gets its value. */
  uploadArtifact?: ((file: File, mediaType: string) => Promise<{ sha256: string }>) | undefined;
  /** Progress and queue state of runs, by run id, joined to a JobStatus here. */
  live?: Record<string, RunLive> | undefined;
};
type InputField = {
  definition: FieldDef;
  value: unknown;
  path: string;
  present: boolean;
  reviewCandidate?: { id: string; status: string } | null;
  intakeBinding?: NonNullable<Capability["intakeBindings"]>[string];
};
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
  appLink,
  uploadArtifact,
  live,
  scope,
  title,
  imageActivity,
}: { kind: string; value: unknown } & WorkspaceActions) {
  if (!value || typeof value !== "object")
    return <p className={s.placeholder}>Waiting for bound data</p>;
  const d = value as Record<string, unknown>;
  if (kind === "SchemaForm" || kind === "ParameterGrid")
    return (
      <SchemaForm
        data={value as InputData}
        onAction={onAction}
        scope={scope}
        uploadArtifact={uploadArtifact}
      />
    );
  if (kind === "JobStatus") return <Job data={d} onAction={onAction} title={title} live={live} />;
  if (kind === "ArtifactView")
    return (
      <section className={s.artifactPanel} aria-label={title || "Artifact workspace"}>
        {Array.isArray(d.items) && d.items.length ? (
          <ArtifactBrowser
            key={`${scope}:${d.items.find((item) => item.source === "input")?.sha256 ?? "outputs"}`}
            items={d.items.filter((item) => item.role !== "sequence-frame")}
            load={loadArtifact}
            appLink={appLink}
            activity={imageActivity ?? "idle"}
          />
        ) : (
          <div className={s.artifactEmpty}>
            <Mark size={36} />
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
  if (kind === "EvidenceView") {
    const items = Array.isArray(d.items) ? (d.items as Record<string, unknown>[]) : [];
    return (
      <section className={s.panel} aria-label={title || String(d.title ?? "Evidence")}>
        <header className={s.panelHead}>
          <h3 className={s.panelTitle}>{title || String(d.title ?? "Evidence")}</h3>
          <span className={s.panelAside}>
            <span className={s.pill} data-tone={EVIDENCE_STATE[String(d.state)]?.tone}>
              {EVIDENCE_STATE[String(d.state)]?.label ?? String(d.state ?? "Nothing yet")}
            </span>
            {items.length ? <span className={s.pill}>{items.length}</span> : null}
          </span>
        </header>
        {items.length ? (
          <ul className={s.evidence}>
            {items.map((item) => (
              <li key={String(item.name)} className={s.evidenceRow}>
                <span className={s.evidenceName} title={String(item.name)}>
                  {String(item.name).replaceAll("_", " ")}
                </span>
                <span className={s.evidenceMeta}>
                  {item.status && item.status !== "current" ? (
                    <span className={s.evidenceStatus}>{String(item.status)}</span>
                  ) : null}
                  {isStamp(item.stamp) ? (
                    <Stamp
                      kind={item.stamp}
                      iconOnly
                      title={`${String(item.status)} · ${String(item.stamp)}`}
                    />
                  ) : (
                    <span className={s.pill}>{String(item.stamp)}</span>
                  )}
                </span>
                <Provenance
                  text={`${String(item.provenanceId)}${item.ledgerId ? ` · ${item.ledgerId}` : ""}`}
                  copy={String(item.provenanceId)}
                  label="provenance id"
                />
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    );
  }
  return null;
}
function isStamp(stamp: unknown): stamp is StampKind {
  return stamp === "verified" || stamp === "warning" || stamp === "blocked" || stamp === "checking";
}

/** Fields past this count fold their optional ones behind a disclosure. */
const FOLD_AFTER = 6;

/** `/twin/{case}/{lane}` when every field of a form sits in one lane, for an agent cursor. */
function lanePath(fields: InputField[]): string | undefined {
  const lanes = new Set(fields.map((f) => f.path.split("/").slice(0, 4).join("/")));
  return lanes.size === 1 ? [...lanes][0] : undefined;
}

function SchemaForm({
  data,
  onAction,
  scope,
  uploadArtifact,
}: { data: InputData } & WorkspaceActions) {
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
  const fieldNode = (field: InputField) => (
    <SchemaField
      key={field.path}
      scope={scope}
      field={field}
      disabled={!data.editable || !onAction || busy !== null}
      onCommit={(v) => edit(field, v)}
      upload={uploadArtifact}
    />
  );
  const isSource = (f: InputField) =>
    f.definition.kind === "artifact" || f.intakeBinding?.valueKind === "artifact";
  const sources = data.fields.filter(isSource);
  const rest = data.fields.filter((f) => !isSource(f));
  const fold =
    data.fields.length > FOLD_AFTER &&
    rest.some((f) => f.definition.required) &&
    rest.filter((f) => !f.definition.required).length > 1;
  const main = fold ? rest.filter((f) => f.definition.required) : rest;
  const optional = fold ? rest.filter((f) => !f.definition.required) : [];
  const grouped = sources.length > 0 && main.length > 0;
  return (
    <section className={s.panel} aria-label={data.title} data-lane={lanePath(data.fields)}>
      <header className={s.panelHead}>
        <h3 className={s.panelTitle}>{data.title}</h3>
        <span className={s.panelAside}>
          <span className={s.pill}>{data.owner ?? "unclaimed"}</span>
          <span className={s.pill} data-tone={data.editable ? "work" : undefined}>
            {data.editable ? "editable" : "read only"}
          </span>
        </span>
      </header>
      {sources.length ? (
        <div className={s.fields}>
          {grouped ? <p className={s.eyebrowRow}>Source</p> : null}
          {sources.map(fieldNode)}
        </div>
      ) : null}
      {main.length ? (
        <div className={s.fields}>
          {grouped ? <p className={s.eyebrowRow}>Parameters</p> : null}
          {main.map(fieldNode)}
        </div>
      ) : null}
      {optional.length ? (
        <details className={s.advanced}>
          <summary>Advanced · {optional.length} optional</summary>
          <div className={s.advancedBody}>{optional.map(fieldNode)}</div>
        </details>
      ) : null}
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
  upload,
}: {
  field: InputField;
  scope?: string | undefined;
  disabled: boolean;
  onCommit: (v: unknown) => Promise<void>;
  upload?: WorkspaceActions["uploadArtifact"];
}) {
  const f = field.definition;
  const display = displayOf(f, field.value);
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
  const [uploading, setUploading] = useState(false);
  // Two surfaces can show the same input (the studio and a chat view), so the id carries the scope.
  const id = `field-${scope ?? "isolated"}:${field.path}`;
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
      const v = parseDraft(f, selected ?? draft, unit, schemaBoundsApply);
      if (JSON.stringify(v) !== JSON.stringify(field.value)) await onCommit(v);
      committed();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid value");
    }
  }
  const pick = (text: string) => {
    setDraft(text);
    void commit(false, text);
  };
  const label = `${f.label || f.name}${unit ? ` (${unit})` : ""}${f.required ? " *" : ""}`;
  const choices = f.choices ?? [];
  let control: ReactNode;
  if (f.review_quantity || f.reviewQuantity) {
    control = (
      <div className={s.reviewField}>
        <p>
          {field.value
            ? "Review recorded for the selected result."
            : "Review the output and its checks before approving."}
        </p>
        {field.value ? <small className={s.prov}>{String(field.value)}</small> : null}
        <Button
          disabled={
            disabled ||
            !field.reviewCandidate ||
            field.reviewCandidate.status !== "current" ||
            field.value === field.reviewCandidate.id
          }
          onClick={() => {
            void onCommit(field.reviewCandidate?.id).catch(() => {});
          }}
        >
          Approve reviewed result
        </Button>
        {field.value ? (
          <Button
            variant="ghost"
            disabled={disabled}
            onClick={() => {
              void onCommit("").catch(() => {});
            }}
          >
            Clear review
          </Button>
        ) : null}
        {!field.value && field.reviewCandidate?.status !== "current" ? (
          <span className={s.note}>A current result is required before approval.</span>
        ) : null}
      </div>
    );
  } else if (field.intakeBinding?.valueKind === "artifact") {
    const binding = field.intakeBinding;
    control = (
      <DropZone
        id={id}
        accept={binding.mediaType}
        disabled={disabled || !upload}
        busy={uploading}
        title={field.present ? "Replace the input" : "Add the input"}
        hint="Drop a file or click. Dependent results must be recomputed."
        onFile={(file) => {
          if (!upload) return;
          setUploading(true);
          setError(null);
          void (async () => {
            try {
              const uploaded = await upload(file, intakeMediaType(binding.mediaType, file.type));
              const previous =
                field.value && typeof field.value === "object" && !Array.isArray(field.value)
                  ? field.value
                  : {};
              await onCommit({ ...previous, artifact: uploaded.sha256 });
            } catch (e) {
              setError(e instanceof Error ? e.message : "Upload failed");
            } finally {
              setUploading(false);
            }
          })();
        }}
      />
    );
  } else {
    switch (f.kind) {
      case "choice":
        control = (
          <select id={id} value={draft} disabled={disabled} onChange={(e) => pick(e.target.value)}>
            <option value="" disabled={!choices.includes("")}>
              Not set
            </option>
            {choices.map((choice) => (
              <option key={choice} value={choice}>
                {choice}
              </option>
            ))}
          </select>
        );
        break;
      case "boolean":
        control = (
          <button
            id={id}
            type="button"
            role="switch"
            aria-checked={draft === "true"}
            disabled={disabled}
            className={b.switch}
            onClick={() => pick(draft === "true" ? "false" : "true")}
          >
            <span className={b.knob} aria-hidden="true" />
            <span className={b.switchText}>{draft === "true" ? "On" : "Off"}</span>
          </button>
        );
        break;
      case "date":
        control = (
          <input
            id={id}
            type="date"
            value={draft}
            disabled={disabled}
            onChange={(e) => {
              if (e.target.value) pick(e.target.value);
              else setDraft("");
            }}
          />
        );
        break;
      case "multichoice": {
        const picked = pickedChoices(safeJson(draft));
        control = (
          <fieldset className={b.chips} disabled={disabled} aria-labelledby={`${id}-label`}>
            {choices.map((choice) => {
              const on = picked.includes(choice);
              return (
                <label key={choice} className={b.chip} data-on={on}>
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() =>
                      pick(
                        JSON.stringify(
                          choices.filter((c) => (c === choice ? !on : picked.includes(c))),
                        ),
                      )
                    }
                  />
                  <span>{choice}</span>
                </label>
              );
            })}
          </fieldset>
        );
        break;
      }
      case "artifact": {
        const sha = artifactSha(field.value);
        control = (
          <div className={b.upload}>
            <DropZone
              id={id}
              disabled={disabled || !upload}
              busy={uploading}
              title={sha ? "Replace the file" : "Choose a file"}
              hint={sha ? `Stored ${sha.slice(0, 10)}…` : "No file yet. Drop one here or click."}
              onFile={(file) => {
                if (!upload) return;
                setUploading(true);
                setError(null);
                upload(file, file.type || "application/octet-stream")
                  .then((stored) => pick(stored.sha256))
                  .catch((err) =>
                    setError(err instanceof Error ? err.message : "The file was not stored."),
                  )
                  .finally(() => setUploading(false));
              }}
            />
          </div>
        );
        break;
      }
      case "list":
      case "object":
        control = (
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
        );
        break;
      case "text":
        control = f.multiline ? (
          <textarea
            id={id}
            className={b.prose}
            rows={4}
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void commit()}
          />
        ) : (
          <input
            id={id}
            type="text"
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => void commit()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commit();
            }}
          />
        );
        break;
      default:
        control = (
          <input
            id={id}
            type="number"
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
        );
    }
  }
  return (
    <div className={s.inputField} data-kind={f.kind} data-field={field.path}>
      <label htmlFor={id} id={`${id}-label`}>
        {label}
      </label>
      {control}
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
/** The evidence state in plain words, with the tone of its pill. */
const EVIDENCE_STATE: Record<
  string,
  { label: string; tone?: "ok" | "warn" | "bad" | "work" | undefined }
> = {
  absent: { label: "Nothing yet" },
  stale: { label: "Out of date", tone: "warn" },
  checking: { label: "Checking", tone: "work" },
  verified: { label: "Verified", tone: "ok" },
  attention_required: { label: "Needs review", tone: "warn" },
  rejected: { label: "Rejected", tone: "bad" },
};
function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return [];
  }
}
function Job({
  data,
  onAction,
  title,
  live,
}: { data: Record<string, unknown>; title?: string | undefined } & WorkspaceActions) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A plan from chat shows a run's state and nothing else: no Run control, ever.
  const readOnly = data.readOnly === true;
  const runs = Array.isArray(data.runs)
    ? (data.runs as { runId: string; tool?: string; status?: string; error?: string | null }[])
    : [];
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
  const state = String(data.state ?? "idle");
  const tone =
    state === "running" || state === "queued"
      ? "work"
      : state === "done"
        ? "ok"
        : state === "failed"
          ? "bad"
          : undefined;
  return (
    <section
      className={s.panel}
      aria-label={title || String(data.title)}
      data-run-case={readOnly ? undefined : String(data.case ?? "")}
    >
      <header className={s.panelHead}>
        <h3 className={s.panelTitle}>{title || String(data.title)}</h3>
        <span className={s.panelAside}>
          <span className={s.pill} data-tone={tone} role="status">
            Execution: {state}
          </span>
          {readOnly ? null : (
            <Button
              type="button"
              small
              disabled={!data.enabled || !onAction || busy}
              onClick={() => void run()}
            >
              {busy ? "Starting…" : "Run"}
            </Button>
          )}
        </span>
      </header>
      {data.description ? <p className={s.jobMeta}>{String(data.description)}</p> : null}
      {runs.length ? <RunList runs={runs} live={live} /> : null}
      {!readOnly && !data.enabled ? (
        <p className={s.jobMeta}>
          Execution requires all lanes to have an owner and no active job.
        </p>
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
  role?: string;
  plugin?: string;
  data?: unknown;
};

type AppLink = WorkspaceActions["appLink"];

/** A result's artifacts. Charts, tables and apps are laid out as a report; anything else, and a
 * result with nothing of those, is the tabbed browser of stored files. */
function ArtifactBrowser({
  items,
  load,
  appLink,
  activity,
}: {
  items: ArtifactDescriptor[];
  load: WorkspaceActions["loadArtifact"];
  appLink: AppLink;
  activity: ImageActivity;
}) {
  const sorted = useClassified(items, load);
  const files = (list: ArtifactDescriptor[]) => (
    <FileBrowser items={list} load={sorted.load} appLink={appLink} activity={activity} />
  );
  if (sorted.ready && isReport(sorted.list))
    return (
      <ArtifactReport
        list={sorted.list}
        appLink={appLink}
        files={files(sorted.list.filter((c) => c.kind === "file").map((c) => c.item))}
      />
    );
  return files(items);
}

function FileBrowser({
  items,
  load,
  appLink,
  activity,
}: {
  items: ArtifactDescriptor[];
  load: WorkspaceActions["loadArtifact"];
  appLink: AppLink;
  activity: ImageActivity;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const identity = (item: ArtifactDescriptor) => item.sha256 ?? item.label ?? "artifact";
  // A run that finishes brings new files: show the first new one at once, instead of staying on
  // the source image. Files present on first render keep the usual first tab.
  const known = useRef<Set<string> | null>(null);
  const ids = items.map(identity).join("|");
  // biome-ignore lint/correctness/useExhaustiveDependencies: `ids` stands for the item list
  useEffect(() => {
    const now = items.map(identity);
    if (known.current === null) {
      known.current = new Set(now);
      return;
    }
    const seen = known.current;
    const fresh = items.find((item) => !seen.has(identity(item)) && item.source !== "input");
    known.current = new Set(now);
    if (fresh) setSelected(identity(fresh));
  }, [ids]);
  const active = items.find((item) => identity(item) === selected) ?? items[0];
  return (
    <div className={s.artifactBrowser}>
      {items.length > 1 ? (
        <div className={s.artifactTabs} role="tablist" aria-label="Stored artifacts">
          <span className={s.tabCount} aria-hidden="true">
            {items.length}
          </span>
          {items.map((item) => (
            <button
              type="button"
              key={identity(item)}
              role="tab"
              aria-selected={item === active}
              tabIndex={item === active ? 0 : -1}
              onKeyDown={(event) => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                const index = items.indexOf(item);
                const next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? items.length - 1
                      : (index + (event.key === "ArrowRight" ? 1 : -1) + items.length) %
                        items.length;
                const target = items[next];
                if (target) setSelected(identity(target));
                event.currentTarget.parentElement
                  ?.querySelectorAll<HTMLButtonElement>("button")
                  [next]?.focus();
              }}
              onClick={() => setSelected(identity(item))}
            >
              {item.source === "input" ? "Source" : (item.label ?? "Artifact").replaceAll("_", " ")}
              {item.source === "reviewed snapshot" ? <span> · reviewed</span> : null}
            </button>
          ))}
        </div>
      ) : null}
      {active ? (
        <div role="tabpanel" aria-label={active.label ?? "Artifact"}>
          <Artifact
            key={identity(active)}
            descriptor={active}
            load={load}
            appLink={appLink}
            activity={activity}
          />
        </div>
      ) : null}
    </div>
  );
}

function Artifact({
  descriptor,
  load,
  appLink,
  activity,
}: {
  descriptor: ArtifactDescriptor;
  load: WorkspaceActions["loadArtifact"];
  appLink: AppLink;
  activity: ImageActivity;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [download, setDownload] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [html, setHtml] = useState(false);
  const [data, setData] = useState<unknown>(descriptor.data);
  const [error, setError] = useState<string | null>(null);
  const isApp = isAppRole(descriptor.role);
  useEffect(() => {
    let active = true;
    let objectUrl: string | undefined;
    setUrl(null);
    setDownload(null);
    setText(null);
    setHtml(false);
    setData(descriptor.data);
    setError(null);
    if (!descriptor.sha256 || !load || isApp) return;
    const sha = descriptor.sha256;
    void load(sha)
      .then(async (blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setDownload(objectUrl);
        if (blob.type.startsWith("image/")) {
          setUrl(objectUrl);
        } else if (/jsonl|ndjson/.test(blob.type)) {
          // One record per line (a dataset): shown as a table, never parsed as one document.
          const parsed = jsonLines(await blob.text());
          if (active) {
            if (typeof parsed === "string") setText(parsed);
            else setData(parsed);
          }
        } else if (blob.type.includes("json")) {
          const parsed: unknown = JSON.parse(await blob.text());
          if (active) setData(parsed);
        } else if (blob.type.startsWith("text/html")) {
          // A page: shown as an app when the room serves it as one, else as its text.
          const content = await blob.text();
          if (!active) return;
          setText(content);
          const served = await (appLink ?? requestAppLink)(sha).then(
            () => true,
            () => false,
          );
          if (active && served) setHtml(true);
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
  }, [descriptor.sha256, descriptor.data, load, appLink, isApp]);
  if ((isApp || html) && descriptor.sha256)
    return (
      <AppFrame
        sha={descriptor.sha256}
        title={prettyLabel(descriptor.label)}
        initialHeight={descriptor.role === "report" ? 640 : 420}
        {...(descriptor.plugin ? { plugin: descriptor.plugin } : {})}
        {...(appLink ? { link: appLink } : {})}
      />
    );
  const sequence = parseImageSequence(data);
  if (!sequence && data !== undefined && parsePlotArtifact(data))
    return (
      <div className={plotStyle.bare}>
        <DataArtifact data={data} />
        <small>
          {descriptor.sha256}
          {descriptor.provenanceId ? ` · source ${descriptor.provenanceId}` : ""}
        </small>
      </div>
    );
  return (
    <figure className={s.artifact}>
      <figcaption>
        {descriptor.label ?? "Artifact"} · {descriptor.source ?? "stored"}
        {descriptor.illustrative ? " · illustrative" : ""}
      </figcaption>
      {url ? (
        <ImageStage
          key={url}
          src={url}
          alt={descriptor.label ?? "Stored artifact"}
          activity={descriptor.source === "input" ? activity : "idle"}
          assemble={descriptor.source === "input"}
        />
      ) : text !== null ? (
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{text}</pre>
      ) : sequence ? (
        <ArtifactSequence sequence={sequence} load={load} />
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
      {descriptor.sha256 || descriptor.provenanceId ? (
        <Provenance
          className={s.artifactProv}
          text={`${descriptor.sha256 ?? ""}${descriptor.provenanceId ? `${descriptor.sha256 ? " · " : ""}source ${descriptor.provenanceId}` : ""}`}
          copy={descriptor.sha256 ?? descriptor.provenanceId ?? ""}
          label={descriptor.sha256 ? "sha256" : "provenance id"}
        />
      ) : null}
    </figure>
  );
}
