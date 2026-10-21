/** A sequence references stored raster artifacts, never external URLs or executable media. */
export type ImageSequence = {
  kind: "image-sequence";
  frames: { sha256: string; label?: string; time?: number }[];
  timeUnit?: string;
  description?: string;
};

export function parseImageSequence(value: unknown): ImageSequence | null {
  if (!value || typeof value !== "object") return null;
  const d = value as Record<string, unknown>;
  if (d.kind !== "image-sequence" || !Array.isArray(d.frames)) return null;
  if (d.frames.length < 2 || d.frames.length > 201) return null;
  if (d.timeUnit !== undefined && (typeof d.timeUnit !== "string" || d.timeUnit.length > 80))
    return null;
  if (
    d.description !== undefined &&
    (typeof d.description !== "string" || d.description.length > 2000)
  )
    return null;
  let previous = -Infinity;
  for (const frame of d.frames) {
    if (!frame || typeof frame !== "object" || Array.isArray(frame)) return null;
    if (typeof frame.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(frame.sha256)) return null;
    if (frame.label !== undefined && (typeof frame.label !== "string" || frame.label.length > 200))
      return null;
    if (frame.time !== undefined) {
      if (typeof frame.time !== "number" || !Number.isFinite(frame.time) || frame.time < previous)
        return null;
      previous = frame.time;
    }
  }
  return value as ImageSequence;
}
