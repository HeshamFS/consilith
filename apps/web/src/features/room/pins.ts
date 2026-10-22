import type { RunLive } from "@/shared/a2ui/types";
import type { RoomDoc, ToolCall } from "./types";

/** A view the person pinned: the refs-only plan the room keeps, never a number. The room binds it
 * to the data again whenever it publishes or refreshes the pin, and the bound surface arrives on
 * the feed under `pin:{id}:{viewer}`, one per person it is shared with. */
export type PinRecord = {
  id: string;
  owner: string;
  title: string;
  origin?: { agent?: string; model?: string };
  case: string;
  lanes: string[];
  pinnedSeq: number;
  refreshedSeq: number;
  shares: Record<string, { editable: boolean; seq: number }>;
};

export const pinSurfaceId = (pinId: string, viewer: string) => `pin:${pinId}:${viewer}`;

type WithPins = RoomDoc & { pins?: Record<string, PinRecord> };

/** The views a person owns or was shared, oldest first. */
export function pinsOf(doc: RoomDoc, person: string): PinRecord[] {
  const pins = (doc as WithPins).pins;
  if (!pins || typeof pins !== "object") return [];
  return Object.values(pins)
    .filter((p) => p && (p.owner === person || !!p.shares?.[person]))
    .sort((a, b) => a.pinnedSeq - b.pinnedSeq);
}

/** Progress a tool reported, by run id, joined to a JobStatus run. The feed keeps a tool call's
 * progress under `call-{runId}`; the queue entry shares the run's id. The room supplies no
 * progress number of its own. */
export function liveRuns(
  tools: Record<string, ToolCall>,
  queue?: Record<string, { status: string; why: string }>,
): Record<string, RunLive> {
  const out: Record<string, RunLive> = {};
  for (const [id, call] of Object.entries(tools)) {
    if (!id.startsWith("call-") || !call.progress) continue;
    out[id.slice("call-".length)] = { progress: call.progress };
  }
  for (const [id, entry] of Object.entries(queue ?? {})) {
    out[id] = { ...out[id], queue: { status: entry.status, why: entry.why } };
  }
  return out;
}
