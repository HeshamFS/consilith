import { useEffect, useRef, useState } from "react";
import s from "./a2ui.module.css";
import type { ImageSequence } from "./imageSequence";
import { SequenceCache } from "./sequenceCache";

/** Playback changes only the displayed stored frame; it never interpolates output data. */
export function ArtifactSequence({
  sequence,
  load,
}: {
  sequence: ImageSequence;
  load: ((sha: string) => Promise<Blob>) | undefined;
}) {
  const cache = useRef<SequenceCache | null>(null);
  const [frames, setFrames] = useState<ReadonlyMap<string, { url: string; extension: string }>>(
    new Map(),
  );
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [decoded, setDecoded] = useState<{ url: string; width: number; height: number } | null>(
    null,
  );
  const frame = sequence.frames[index];
  const stored = frame && frames.get(frame.sha256);
  const url = stored?.url;
  const error = frame && errors[frame.sha256];
  useEffect(() => {
    setFrames(new Map());
    setIndex(0);
    setPlaying(false);
    setErrors({});
    setDecoded(null);
    if (!load) return;
    const session = new SequenceCache(sequence, load, setFrames, (hash, message) => {
      setErrors((current) => ({ ...current, [hash]: message }));
    });
    cache.current = session;
    session.request(0);
    return () => {
      session.dispose();
      cache.current = null;
    };
  }, [sequence, load]);
  useEffect(() => {
    cache.current?.request(index);
  }, [index]);
  useEffect(() => {
    if (error) setPlaying(false);
  }, [error]);
  useEffect(() => {
    if (!playing || !url || decoded?.url !== url || error) return;
    const timer = window.setTimeout(() => {
      if (index >= sequence.frames.length - 1) setPlaying(false);
      else setIndex(index + 1);
    }, 180 / speed);
    return () => window.clearTimeout(timer);
  }, [playing, url, decoded, error, index, sequence.frames.length, speed]);
  return (
    <div className={s.sequence}>
      {sequence.description ? <p>{sequence.description}</p> : null}
      <div className={s.sequenceFrame}>
        {url ? (
          <img
            src={url}
            alt={frame?.label ?? `Stored frame ${index + 1}`}
            onLoad={(event) =>
              setDecoded({
                url,
                width: event.currentTarget.naturalWidth,
                height: event.currentTarget.naturalHeight,
              })
            }
            onError={() => {
              if (frame)
                setErrors((current) => ({
                  ...current,
                  [frame.sha256]: "Stored frame could not be decoded",
                }));
              setPlaying(false);
            }}
          />
        ) : (
          <p role="status">{load ? "Loading stored frame…" : "Artifact connection unavailable"}</p>
        )}
      </div>
      <div className={s.sequenceControls}>
        <button
          type="button"
          disabled={!load || (!playing && (!!error || !url))}
          onClick={() => {
            if (index === sequence.frames.length - 1) setIndex(0);
            setPlaying(!playing);
          }}
        >
          {playing ? "Pause" : index === sequence.frames.length - 1 ? "Replay" : "Play"}
        </button>
        <button
          type="button"
          disabled={!load}
          onClick={() => {
            setPlaying(false);
            setIndex(0);
          }}
        >
          First frame
        </button>
        <label>
          Playback speed{" "}
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {[0.5, 1, 2, 4].map((value) => (
              <option key={value} value={value}>
                {value}×
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className={s.sequencePosition}>
        <span>
          Frame {index + 1} / {sequence.frames.length}
          {frame?.time !== undefined
            ? ` · ${frame.time.toPrecision(5)} ${sequence.timeUnit ?? ""}`
            : ""}
        </span>
        <input
          aria-label="Sequence frame"
          type="range"
          min={0}
          max={sequence.frames.length - 1}
          step={1}
          value={index}
          disabled={!load}
          onChange={(e) => {
            setPlaying(false);
            setIndex(Number(e.target.value));
          }}
        />
      </label>
      {url && stored ? (
        <a href={url} download={`frame-${index + 1}.${stored.extension}`}>
          Download original frame
          {decoded?.url === url ? ` (${decoded.width} × ${decoded.height})` : ""}
        </a>
      ) : null}
      <p className={s.sequenceNote}>
        Original stored frames · loaded on demand · playback speed does not change calculation time.
      </p>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}
