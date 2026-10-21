import { type PointerEvent, useEffect, useRef, useState } from "react";
import { intakeMediaType } from "@/shared/a2ui/artifactIntake";
import { ImageStage } from "@/shared/a2ui/ImageStage";
import { intakeId } from "@/shared/domain/disciplines";
import { Button, Icon } from "@/shared/ui";
import { Sheet } from "./Sheet";
import s from "./start.module.css";
import type { RoomConnection } from "./store";
import type { CaseId, RoomDoc } from "./types";

type Routed =
  | { supported: true; case: CaseId; label: string; grammar: string }
  | { supported: false; cannot: string; question: string; closest: CaseId | null };
/** Intake bindings, metadata and scientific labels come exclusively from installed packages. */
export function StartSheet({
  conn,
  doc,
  me,
  initial,
  onRouted,
  onClose,
}: {
  conn: RoomConnection;
  doc: RoomDoc;
  me: string;
  initial: string;
  onRouted: (c: CaseId) => void;
  onClose: () => void;
}) {
  const choices = (doc.room.capabilities ?? []).flatMap((cap) =>
    Object.entries(cap.intakeBindings ?? {}).map(([kind, binding]) => ({
      id: intakeId(cap.id, kind),
      kind,
      binding,
      cap,
    })),
  );
  const [kind, setKind] = useState(initial);
  const chosen = choices.find((c) => c.id === kind);
  const [words, setWords] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (!file || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const [metadata, setMetadata] = useState<Record<string, string>>({});
  const [strokes, setStrokes] = useState<number[][][]>([]);
  const drawing = useRef<number[][] | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [routed, setRouted] = useState<Routed | null>(null);
  function point(e: PointerEvent<SVGSVGElement>) {
    const rect = svg.current?.getBoundingClientRect();
    return rect
      ? [
          Math.round(((e.clientX - rect.left) / rect.width) * 600),
          Math.round(((e.clientY - rect.top) / rect.height) * 360),
        ]
      : [0, 0];
  }
  async function send() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      if (!chosen) {
        if (!words.trim()) throw new Error("Describe what you want to do");
        setRouted(await conn.intake({ text: words.trim() }));
        return;
      }
      const { binding, cap } = chosen;
      if (doc.lanes[cap.id]?.[binding.lane] !== me)
        throw new Error("The declared input lane belongs to another person or is unclaimed");
      let value: unknown;
      if (binding.valueKind === "strokes") {
        if (!strokes.length) throw new Error("Draw input first");
        value = strokes;
      } else if (binding.valueKind === "artifact") {
        if (!file) throw new Error("Choose an input file");
        // The server owns format acceptance; packages declare their semantic file type.
        const declared = intakeMediaType(binding.mediaType, file.type);
        const extra: Record<string, unknown> = {};
        for (const f of binding.metadataFields ?? []) {
          const raw = metadata[f.name] ?? "";
          if (f.required && !raw.trim()) throw new Error(`${f.label} is required`);
          if (!raw.trim()) continue;
          if (f.kind === "number") {
            const n = Number(raw);
            if (
              !Number.isFinite(n) ||
              (f.minimumExclusive !== undefined && n <= f.minimumExclusive)
            )
              throw new Error(`${f.label} is outside its declared range`);
            extra[f.name] = n;
          } else extra[f.name] = raw;
        }
        const artifact = await conn.upload(file, declared);
        value = { artifact: artifact.sha256, ...extra };
      } else throw new Error("No installed input editor supports this binding");
      const path = `/twin/${cap.id}/${binding.lane}/${binding.field}`;
      const present = Object.hasOwn(doc.twin[cap.id]?.[binding.lane] ?? {}, binding.field);
      await conn.editTwin(
        [{ op: present ? "replace" : "add", path, value }],
        "User supplied input through declared package binding",
      );
      setRouted({
        supported: true,
        case: cap.id,
        label: binding.label ?? kind,
        grammar: cap.grammar,
      });
      setNote("Input stored. Open the workspace to review controls before execution.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Input failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet
      title="Start from anything"
      sub="Choose an installed input editor or describe your request."
      icon={<Icon name="formula" size={28} />}
      onClose={onClose}
    >
      <div className={s.tabs} role="tablist" aria-label="Input kind">
        {[
          ...choices.map((c) => ({
            id: c.id,
            label: `${c.binding.label ?? c.kind} · ${c.cap.label}`,
          })),
          { id: "words", label: "Describe your request" },
        ].map((c) => (
          <button
            key={c.id}
            type="button"
            role="tab"
            aria-selected={kind === c.id}
            className={s.tab}
            onClick={() => {
              setKind(c.id);
              setError(null);
              setRouted(null);
              setFile(null);
              setMetadata({});
            }}
          >
            {c.label}
          </button>
        ))}
      </div>
      <div className={s.body}>
        {chosen?.binding.valueKind === "strokes" ? (
          <>
            <svg
              ref={svg}
              className={s.pad}
              viewBox="0 0 600 360"
              role="img"
              aria-label="Drawing pad"
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                drawing.current = [point(e)];
                setStrokes((prev) => [...prev, drawing.current as number[][]]);
              }}
              onPointerMove={(e) => {
                if (drawing.current) {
                  drawing.current.push(point(e));
                  setStrokes((prev) => [
                    ...prev.slice(0, -1),
                    [...(drawing.current as number[][])],
                  ]);
                }
              }}
              onPointerUp={() => {
                drawing.current = null;
              }}
            >
              {strokes.map((stroke, i) => (
                <polyline
                  // biome-ignore lint/suspicious/noArrayIndexKey: strokes append until cleared
                  key={`stroke-${i}`}
                  points={stroke.map((p) => p.join(",")).join(" ")}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="3"
                />
              ))}
            </svg>
            <Button variant="ghost" onClick={() => setStrokes([])}>
              Clear
            </Button>
          </>
        ) : chosen?.binding.valueKind === "artifact" ? (
          <>
            <label className={s.drop}>
              {chosen.binding.label ?? "Choose input"}
              <input
                type="file"
                key={kind}
                accept={chosen.binding.mediaType}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            {preview ? (
              <ImageStage
                key={preview}
                src={preview}
                alt="Selected input preview"
                activity={busy ? "uploading" : error ? "failed" : "idle"}
              />
            ) : null}
            {file ? (
              <p className={s.small}>
                {file.name} · {Math.ceil(file.size / 1024)} KiB · original file preserved
              </p>
            ) : null}
            {(chosen.binding.metadataFields ?? []).map((f) => (
              <label key={f.name}>
                {f.label}
                <input
                  type={f.kind === "number" ? "number" : "text"}
                  step="any"
                  value={metadata[f.name] ?? ""}
                  onChange={(e) => setMetadata((prev) => ({ ...prev, [f.name]: e.target.value }))}
                />
              </label>
            ))}
          </>
        ) : (
          <label>
            What do you want to study?
            <textarea
              className={s.words}
              rows={4}
              value={words}
              onChange={(e) => setWords(e.target.value)}
              maxLength={2000}
            />
          </label>
        )}
        {chosen ? <p className={s.small}>{chosen.cap.grammar}</p> : null}
        <Button disabled={busy} onClick={() => void send()}>
          {busy ? "Preparing…" : "Prepare workspace"}
        </Button>
        {error ? (
          <p role="alert" className={s.bad}>
            {error}
          </p>
        ) : null}
        {routed?.supported ? (
          <div role="status">
            <p>{note ?? routed.grammar}</p>
            <Button onClick={() => onRouted(routed.case)}>
              Open {doc.room.capabilities?.find((c) => c.id === routed.case)?.label ?? routed.case}
            </Button>
          </div>
        ) : routed ? (
          <p role="status">
            {routed.cannot} {routed.question}
          </p>
        ) : null}
      </div>
    </Sheet>
  );
}
