/** The room feed as a pure reducer over AG-UI events (agui.py feed_stream):
 * RUN_STARTED, STATE_SNAPSHOT, ACTIVITY_SNAPSHOT a2ui-surface, STATE_DELTA, CUSTOM
 * consilith.{log,wiring,progress,presence}, TOOL_CALL_*, RUN_FINISHED(cancelled) on rotation. */

import { applySurface } from "@/shared/a2ui/surface";
import type { A2UIMessage, Surface } from "@/shared/a2ui/types";
import { applyPatch, type PatchOp } from "@/shared/agui/patch";
import type { AguiEvent } from "@/shared/agui/sse";
import type { LogEvent, Presence, RoomDoc, ToolCall, WiringEvent } from "./types";

export type Connection = "connecting" | "live" | "reconnecting" | "offline";

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

function custom(s: FeedState, name: string, value: unknown): FeedState {
  if (!value || typeof value !== "object") return s;
  switch (name) {
    case "consilith.log":
      return { ...s, log: tail(s.log, value as LogEvent) };
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
