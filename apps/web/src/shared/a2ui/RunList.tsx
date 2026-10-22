import b from "./blocks.module.css";
import type { RunLive } from "./types";

/** The runs of a JobStatus: each run's stored state, joined here with what the page knows while
 * it works: the progress its tool reported (`tools["call-" + runId].progress` in the feed) and
 * its queue entry. The server supplies no progress number; a bar is drawn only from the
 * tool's own report, and a run that has reported none shows a moving bar, not a guess. */

export type RunRow = {
  runId: string;
  tool?: string;
  status?: string;
  error?: string | null;
};

type Tone = "work" | "ok" | "bad" | "idle";

const ACTIVE = new Set(["running", "queued", "started", "pending", "working"]);
const GOOD = new Set(["done", "complete", "completed", "ok", "succeeded", "success", "finished"]);
const BAD = new Set(["failed", "error", "cancelled", "canceled", "timeout", "rejected"]);

export function toneOf(status: string | undefined): Tone {
  const s = (status ?? "").toLowerCase();
  if (ACTIVE.has(s)) return "work";
  if (GOOD.has(s)) return "ok";
  if (BAD.has(s)) return "bad";
  return "idle";
}

/** 0..1 when the tool reported a total, null when it reported none (an unknown amount). */
export function fraction(live: RunLive | undefined): number | null {
  const p = live?.progress;
  if (!p || !(p.total && p.total > 0) || !Number.isFinite(p.progress)) return null;
  return Math.max(0, Math.min(1, p.progress / p.total));
}

export function RunList({
  runs,
  live,
}: {
  runs: RunRow[];
  live?: Record<string, RunLive> | undefined;
}) {
  return (
    <ul className={b.runs} aria-label="Runs">
      {runs.map((r) => {
        const extra = live?.[r.runId];
        const queued = extra?.queue && !GOOD.has(extra.queue.status) ? extra.queue : undefined;
        const state = queued && toneOf(r.status) !== "ok" ? queued.status : (r.status ?? "unknown");
        const tone = toneOf(state);
        const frac = fraction(extra);
        const working = tone === "work";
        return (
          <li key={r.runId} className={b.run} data-tone={tone} title={`run ${r.runId}`}>
            <div className={b.runHead}>
              <span className={b.runTool}>{r.tool || "Run"}</span>
              <span className={b.runState} data-tone={tone}>
                {working ? <i className={b.pulse} aria-hidden="true" /> : null}
                {state}
              </span>
            </div>
            {working ? (
              <div
                className={b.track}
                role="progressbar"
                aria-label={`${r.tool || "Run"} progress`}
                aria-valuemin={0}
                aria-valuemax={100}
                {...(frac === null ? {} : { "aria-valuenow": Math.round(frac * 100) })}
                data-unknown={frac === null}
              >
                <span
                  className={b.fill}
                  style={frac === null ? undefined : { width: `${(frac * 100).toFixed(1)}%` }}
                />
              </div>
            ) : null}
            {extra?.progress?.message || queued?.why ? (
              <p className={b.runNote}>{extra?.progress?.message ?? queued?.why}</p>
            ) : null}
            {r.error ? (
              <p className={b.runError} role="alert">
                {r.error}
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
