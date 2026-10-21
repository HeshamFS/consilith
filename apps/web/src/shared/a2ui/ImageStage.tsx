import { type CSSProperties, useState } from "react";
import s from "./image-stage.module.css";

export type ImageActivity = "idle" | "uploading" | "queued" | "running" | "done" | "failed";

/** Presentation of supplied pixels. Activity is indeterminate and follows the host job. */
export function ImageStage({
  src,
  alt,
  activity = "idle",
  assemble = true,
}: {
  src: string;
  alt: string;
  activity?: ImageActivity;
  assemble?: boolean;
}) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [size, setSize] = useState("");
  const [replay, setReplay] = useState(0);
  const active = activity === "running" || activity === "uploading";
  const status = {
    idle: "Image ready",
    uploading: "Uploading image",
    queued: "Queued · waiting to start",
    running: "Processing · waiting for results",
    done: "Processing complete · inspect the results",
    failed: "Processing stopped · check the run message",
  }[activity];
  return (
    <div className={s.stage} data-activity={activity} aria-busy={active}>
      <div className={s.toolbar}>
        <span className={s.overline}>Image workspace</span>
        {size ? <span className={s.dimensions}>{size} px</span> : null}
        {loaded && assemble ? (
          <button type="button" onClick={() => setReplay((value) => value + 1)}>
            Replay image assembly
          </button>
        ) : null}
      </div>
      <div className={s.canvas}>
        <img
          className={s.image}
          src={src}
          alt={alt}
          onLoad={(event) => {
            setLoaded(true);
            setSize(`${event.currentTarget.naturalWidth} × ${event.currentTarget.naturalHeight}`);
          }}
          onError={() => setFailed(true)}
        />
        {loaded && assemble && !failed ? (
          <div className={s.assembly} key={replay} aria-hidden="true">
            {Array.from({ length: 16 }, (_, index) => {
              const x = index % 4;
              const y = Math.floor(index / 4);
              return (
                <div
                  key={`${x}-${y}`}
                  className={s.tile}
                  style={
                    {
                      clipPath: `inset(${y * 25}% ${(3 - x) * 25}% ${(3 - y) * 25}% ${x * 25}%)`,
                      "--delay": `${(x + y) * 45}ms`,
                      "--drift-x": `${(x - 1.5) * 18}px`,
                      "--drift-y": `${(y - 1.5) * 18}px`,
                      backgroundImage: `url("${src}")`,
                    } as CSSProperties
                  }
                />
              );
            })}
          </div>
        ) : null}
        {active && loaded && !failed ? (
          <div className={s.scanner} aria-hidden="true">
            <div className={s.scanGrid} />
            <div className={s.scanBeam} />
            <div className={s.targetTop} />
            <div className={s.targetBottom} />
          </div>
        ) : null}
        {failed ? (
          <p role="alert">Image could not be displayed. Download the original below.</p>
        ) : null}
      </div>
      <div className={s.status} role="status">
        <span className={s.statusDot} aria-hidden="true" />
        <span>{failed ? "Image preview unavailable" : loaded ? status : "Loading image…"}</span>
        <span className={s.preserved}>Full image · aspect ratio preserved</span>
      </div>
    </div>
  );
}
