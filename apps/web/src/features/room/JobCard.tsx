import { createContext, useContext, useEffect, useState } from "react";
import type { JobLive } from "./feed";
import s from "./jobcard.module.css";

/** The fixed sentence the "Finish my request" chip sends. It names no number and no result. */
export const FINISH_REQUEST_SENTENCE =
  "Please finish my request with the results that just came in.";

/** What a job card needs from its surroundings: the live job (queue entry plus the run's
 * progress, from the room feed) and a way to send a chat message. Both are optional, so the card
 * still renders from the chat event alone. */
export type JobBridge = {
  live?: ((jobId: string) => JobLive | undefined) | undefined;
  send?: ((text: string) => void) | undefined;
};

export const JobBridgeContext = createContext<JobBridge>({});

export type JobCardData = {
  kind: "job";
  id: string;
  jobId: string;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  message: string;
  progress?: number | undefined;
};

const STATUS_LABEL: Record<JobCardData["status"], string> = {
  queued: "Waiting its turn",
  running: "Running",
  done: "Finished",
  failed: "Did not finish",
  cancelled: "Cancelled",
};

/** "about 40 s left", "about 3 min left". No number below a second, none past a day. */
export function formatEta(etaS: number): string {
  if (etaS < 5) return "almost done";
  if (etaS < 90) return `about ${Math.round(etaS / 5) * 5} s left`;
  if (etaS < 3600) return `about ${Math.round(etaS / 60)} min left`;
  return `about ${Math.round(etaS / 360) / 10} h left`;
}

function useTick(active: boolean): void {
  const [, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setN((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

export function JobCardView({
  card,
  onFix,
}: {
  card: JobCardData;
  onFix?: ((action: string) => void) | undefined;
}) {
  const bridge = useContext(JobBridgeContext);
  const live = bridge.live?.(card.jobId);
  const status = live?.status ?? card.status;
  const active = status === "running" || status === "queued";
  useTick(status === "running");

  const fraction = live ? live.fraction : (card.progress ?? null);
  const message = live?.message ?? card.message;
  const pct = fraction === null ? null : Math.round(fraction * 100);
  const finish = () => {
    if (bridge.send) bridge.send(FINISH_REQUEST_SENTENCE);
    else onFix?.("continue");
  };

  return (
    <article
      className={s.job}
      data-status={status}
      aria-label={`Job: ${card.title}`}
      aria-live="polite"
    >
      <header className={s.head}>
        <span className={s.title}>{card.title}</span>
        <span className={s.chip} data-status={status}>
          {STATUS_LABEL[status]}
        </span>
      </header>
      {active || status === "done" ? (
        <progress
          className={s.bar}
          max={1}
          {...(status === "queued" || fraction === null ? {} : { value: fraction })}
          aria-label={`${card.title} progress`}
        />
      ) : null}
      {status === "running" || status === "done" ? (
        <div className={s.meta}>
          <span className={s.note}>{message || (pct === null ? "Working…" : "")}</span>
          <span className={s.figures}>
            {pct !== null ? <span>{pct}%</span> : null}
            {live?.etaS !== undefined ? <span>{formatEta(live.etaS)}</span> : null}
          </span>
        </div>
      ) : message ? (
        <p className={s.note}>{message}</p>
      ) : null}
      {status === "failed" ? (
        <p className={s.problem}>{live?.error || "It stopped before it finished."}</p>
      ) : null}
      {live?.supersedes ? (
        <p className={s.note}>Started again after a restart; the earlier attempt was closed.</p>
      ) : null}
      {status === "done" && (bridge.send || onFix) ? (
        <button type="button" className={s.finish} onClick={finish}>
          Finish my request
        </button>
      ) : null}
    </article>
  );
}
