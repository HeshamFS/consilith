/** The room feed as a pure reducer over AG-UI events (agui.py feed_stream):
 * RUN_STARTED, STATE_SNAPSHOT, ACTIVITY_SNAPSHOT a2ui-surface, STATE_DELTA, CUSTOM
 * consilith.{log,wiring,progress,presence}, TOOL_CALL_*, RUN_FINISHED(cancelled) on rotation. */

import { applySurface } from "@/shared/a2ui/surface";
import type { A2UIMessage, Surface } from "@/shared/a2ui/types";
import { applyPatch, type PatchOp } from "@/shared/agui/patch";
import type { AguiEvent } from "@/shared/agui/sse";
import type { LogEvent, Presence, RoomDoc, ToolCall, WiringEvent } from "./types";

export type Connection = "connecting" | "live" | "reconnecting" | "offline";

/** The latest peer message the room log reported (ids only: the body is fetched from the thread
 * route). The chat strip uses it to pulse when a colleague's agent answers. */
export type PeerPulse = {
  thread: string;
  msgId: string;
  from: string;
  to: string;
  kind: string;
  hop: number;
  ts: number;
  seq: number;
};

export type FeedState = {
  doc: RoomDoc | null;
  seq: number;
  surfaces: Record<string, Surface>;
  log: LogEvent[];
  wiring: WiringEvent[];
  presence: Record<string, Presence>;
  tools: Record<string, ToolCall>;
  connection: Connection;
  error: string | null;
  skipped: number;
  peerPulse?: PeerPulse | null;
};

const KEEP = 200;

export const initialFeed: FeedState = {
  doc: null,
  seq: 0,
  surfaces: {},
  log: [],
  wiring: [],
  presence: {},
  tools: {},
  connection: "connecting",
  error: null,
  skipped: 0,
};

function tail<T>(list: T[], item: T): T[] {
  const next = list.length >= KEEP ? list.slice(list.length - KEEP + 1) : list.slice();
  next.push(item);
  return next;
}

export function reduceFeed(s: FeedState, e: AguiEvent): FeedState {
  switch (e.type) {
    case "RUN_STARTED":
      return { ...s, error: null };
    case "STATE_SNAPSHOT": {
      // A new run restates the whole room: everything derived from the old run is reset.
      const meta = (e.metadata ?? {}) as { seq?: number };
      return {
        ...s,
        doc: e.snapshot as RoomDoc,
        seq: meta.seq ?? 0,
        surfaces: {},
        tools: {},
        connection: "live",
      };
    }
    case "STATE_DELTA": {
      if (!s.doc) return s;
      const r = applyPatch(s.doc, (e.delta ?? []) as PatchOp[]);
      const meta = (e.metadata ?? {}) as { seq?: number };
      if (!r.ok) return { ...s, skipped: s.skipped + 1 };
      return { ...s, doc: r.doc, seq: meta.seq ?? s.seq };
    }
    case "ACTIVITY_SNAPSHOT": {
      if (e.activityType !== "a2ui-surface") return s;
      const c = (e.content ?? {}) as { surfaceId?: string; messages?: A2UIMessage[] };
      if (!c.surfaceId || !Array.isArray(c.messages)) return s;
      const base = e.replace === false ? s.surfaces[c.surfaceId] : undefined;
      const next = applySurface(base, c.surfaceId, c.messages);
      const surfaces = { ...s.surfaces };
      if (next) surfaces[c.surfaceId] = next;
      else delete surfaces[c.surfaceId];
      return { ...s, surfaces };
    }
    case "TOOL_CALL_START": {
      const id = String(e.toolCallId);
      return {
        ...s,
        tools: {
          ...s.tools,
          [id]: { id, name: String(e.toolCallName ?? ""), args: "", done: false },
        },
      };
    }
    case "TOOL_CALL_ARGS": {
      const id = String(e.toolCallId);
      const t = s.tools[id];
      if (!t) return s;
      return { ...s, tools: { ...s.tools, [id]: { ...t, args: t.args + String(e.delta ?? "") } } };
    }
    case "TOOL_CALL_END": {
      const id = String(e.toolCallId);
      const t = s.tools[id];
      return t ? { ...s, tools: { ...s.tools, [id]: { ...t, done: true } } } : s;
    }
    case "TOOL_CALL_RESULT": {
      const id = String(e.toolCallId);
      const t = s.tools[id] ?? { id, name: "", args: "", done: true };
      return {
        ...s,
        tools: { ...s.tools, [id]: { ...t, done: true, result: String(e.content ?? "") } },
      };
    }
    case "CUSTOM":
      return custom(s, String(e.name), e.value);
    case "RUN_ERROR":
      return { ...s, error: String(e.message ?? "The feed failed."), connection: "reconnecting" };
    case "RUN_FINISHED":
      return { ...s, connection: "reconnecting" };
    default:
      return s;
  }
}

function pulseOf(e: LogEvent): PeerPulse {
  const m = e.meta ?? {};
  return {
    thread: String(m.thread ?? ""),
    msgId: String(m.msgId ?? ""),
    from: String(m.from ?? ""),
    to: String(m.to ?? ""),
    kind: String(m.kind ?? ""),
    hop: Number(m.hop ?? 1),
    ts: e.ts,
    seq: e.seq,
  };
}

function custom(s: FeedState, name: string, value: unknown): FeedState {
  if (!value || typeof value !== "object") return s;
  switch (name) {
    case "consilith.log": {
      const entry = value as LogEvent;
      const next = { ...s, log: tail(s.log, entry) };
      return entry.kind === "peer.message" ? { ...next, peerPulse: pulseOf(entry) } : next;
    }
    case "consilith.wiring":
      return { ...s, wiring: tail(s.wiring, value as WiringEvent) };
    case "consilith.presence": {
      const p = value as Presence;
      return p.person ? { ...s, presence: { ...s.presence, [p.person]: p } } : s;
    }
    case "consilith.progress": {
      const p = value as {
        callId?: string | null;
        progress: number;
        total?: number;
        message?: string;
      };
      if (!p.callId) return s;
      const t = s.tools[p.callId] ?? { id: p.callId, name: "", args: "", done: false };
      const progress: NonNullable<ToolCall["progress"]> = { progress: p.progress };
      if (p.total !== undefined) progress.total = p.total;
      if (p.message !== undefined) progress.message = p.message;
      return { ...s, tools: { ...s.tools, [p.callId]: { ...t, progress } } };
    }
    default:
      return s;
  }
}

// --- Long-run jobs: what a job card shows (WS-Q). Additive; nothing above reads these. --------

export type JobStatusWire = "queued" | "running" | "done" | "failed" | "cancelled";

/** The queue entry the room doc carries for a job (agents/queue.py), beyond `types.ts`. */
export type QueueEntryWire = {
  id: string;
  status: string;
  case?: string;
  why?: string;
  runId?: string;
  startedAt?: number;
  estimateS?: number;
  supersedes?: string;
  error?: string;
  result?: { error?: string | null };
  progress?: { fraction: number | null; message?: string; updatedAt?: number };
};

export type JobLive = {
  status: JobStatusWire;
  /** 0..1, or null while the run reports no total. */
  fraction: number | null;
  message?: string;
  /** Seconds left, when it can be said honestly; otherwise undefined. */
  etaS?: number;
  elapsedS?: number;
  error?: string;
  /** Set when a restart replaced this job's run. */
  supersedes?: string;
};

const JOB_STATUSES: JobStatusWire[] = ["queued", "running", "done", "failed", "cancelled"];

/** Seconds left from the fraction done and the time spent, else from a declared estimate. */
export function etaSeconds(
  fraction: number | null,
  elapsedS: number | undefined,
  estimateS: number | undefined,
): number | undefined {
  if (elapsedS === undefined) return undefined;
  if (fraction !== null && fraction >= 0.03 && fraction < 1) {
    return Math.round((elapsedS * (1 - fraction)) / fraction);
  }
  if (fraction === null && estimateS !== undefined && elapsedS < estimateS) {
    return Math.round(estimateS - elapsedS);
  }
  return undefined;
}

/** The live view of a job: its queue entry in the room doc, with the progress of the run's tool
 * call (`call-{runId}`, carried by consilith.progress) when that is newer than the entry's. */
export function jobLive(
  s: Pick<FeedState, "doc" | "tools">,
  jobId: string,
  nowS: number = Date.now() / 1000,
): JobLive | undefined {
  const entry = (s.doc?.queue as Record<string, QueueEntryWire> | undefined)?.[jobId];
  if (!entry) return undefined;
  const status = JOB_STATUSES.includes(entry.status as JobStatusWire)
    ? (entry.status as JobStatusWire)
    : "queued";
  let fraction: number | null = entry.progress?.fraction ?? null;
  let message = entry.progress?.message;
  const call = entry.runId ? s.tools[`call-${entry.runId}`]?.progress : undefined;
  if (status === "running" && call) {
    if (call.total && call.total > 0)
      fraction = Math.min(1, Math.max(0, call.progress / call.total));
    if (call.message) message = call.message;
  }
  if (status === "done") fraction = 1;
  const elapsedS =
    status === "running" && entry.startedAt !== undefined
      ? Math.max(0, nowS - entry.startedAt)
      : undefined;
  const live: JobLive = { status, fraction };
  if (message) live.message = message;
  if (elapsedS !== undefined) live.elapsedS = elapsedS;
  if (status === "running") {
    const eta = etaSeconds(fraction, elapsedS, entry.estimateS);
    if (eta !== undefined) live.etaS = eta;
  }
  const error = entry.error ?? entry.result?.error;
  if (error) live.error = error;
  if (entry.supersedes) live.supersedes = entry.supersedes;
  return live;
}
