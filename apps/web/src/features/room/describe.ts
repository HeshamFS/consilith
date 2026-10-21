import type { LogEvent, PersonalAgent, RoomDoc } from "./types";

/** Plain words for one log row, in the room's own terms. */
export function describeLog(e: LogEvent, doc: RoomDoc | null): string {
  const m = e.meta ?? {};
  const who = nameOf(e.actor, doc);
  const lane = [m.case, m.lane].filter(Boolean).join("/");
  switch (e.kind) {
    case "room.created":
      return `${who} opened the room`;
    case "room.join":
      return `${who} joined the room`;
    case "lane.claim":
      return `${who} claimed ${lane || "a lane"}`;
    case "lane.release":
      return `${who} released ${lane || "a lane"}`;
    case "twin.edit":
      return `${who} edited ${lane || "the twin"}${m.reason ? `: ${String(m.reason)}` : ""}`;
    case "agent.config":
      return `${who} changed an agent drawer`;
    case "agent.status":
      return `${who} is ${String(m.status ?? "busy")}${m.statusDetail ? `: ${String(m.statusDetail)}` : ""}`;
    case "agent.brought":
      return `${who} brought their own A2A agent`;
    case "agent.brought.refused":
    case "agent.brought.unreachable":
      return `${who}'s A2A card was refused`;
    case "proposal.open":
      return `${who} proposed a change in ${lane || "a lane"}`;
    case "proposal.decide":
      return `${who} ${m.accept ? "accepted" : "declined"} a proposal`;
    case "warning.open":
      return `${who} raised a warning in ${lane || "a lane"}`;
    case "warning.ack":
      return `${who} acknowledged a warning`;
    case "run.open":
      return `${who} started ${String(m.tool ?? "a solve")}`;
    case "run.results":
      return `Results arrived from ${String(m.tool ?? "a solver")}`;
    case "run.failed":
      return `${String(m.tool ?? "A solve")} failed${m.error ? `: ${String(m.error)}` : ""}`;
    case "ledger.entry":
      return "The referee signed a result into the ledger";
    case "intake.routed":
      return `${who}'s input went to ${String(m.case ?? "a studio")}`;
    default:
      return `${who} · ${e.kind.replace(/\./g, " ")}`;
  }
}

export function nameOf(id: string, doc: RoomDoc | null): string {
  if (!doc) return id;
  const p = doc.room.people[id];
  if (p) return p.name || p.id;
  return id;
}

export function agentOf(doc: RoomDoc, person: string): PersonalAgent | null {
  const a = doc.room.agents[`${person}.agent`];
  return a && a.kind === "personal" ? a : null;
}

export function ago(ts: number, now = Date.now()): string {
  const s = Math.max(0, Math.round(now / 1000 - ts));
  if (s < 10) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}
