/** Agent-to-agent threads on the web: the wire shapes, what the room document carries, and the
 * calls to the thread routes (apps/room, domain/peers.py and api/routes_history.py).
 *
 * The room document holds only a small index per thread (ids, counters, states). Bodies are
 * fetched from the thread route by seated people and refetched when the index counter moves. */

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/shared/http/client";
import { agentOf, nameOf } from "./describe";
import type { PeerThreadIndex, RoomDoc } from "./types";

/** The hop and message caps the server enforces (contracts/peer.py). */
export const MAX_HOPS = 4;
export const MAX_MESSAGES = 12;

export type PeerKind = "question" | "answer" | "fyi" | "handoff";
export type NoticeCode = "undeliverable" | "capped" | "paused" | "declined" | "closed" | "refused";

export type PeerNotice = { code: NoticeCode; text: string };
export type PeerPending = { agent: string; msgId: string };

/** A thread as the room document carries it. The first five fields are the F4 shape; the rest
 * were added with the conductor relay and may be absent on an older document. */
export type PeerThread = PeerThreadIndex & {
  agents: string[];
  opener: string;
  hop: number;
  maxHops: number;
  maxMessages: number;
  pending?: PeerPending;
  notice?: PeerNotice;
};

export type PeerPrefs = {
  accepts: boolean;
  paused: boolean;
  lessons: boolean;
  muted: string[];
  contacts: string[];
};

export const DEFAULT_PREFS: PeerPrefs = {
  accepts: false,
  paused: false,
  lessons: false,
  muted: [],
  contacts: [],
};

export type PeerRef = { kind: "case" | "lane" | "result"; id: string; case?: string };

export type PeerMessage = {
  id: string;
  from: { agent: string; person: string };
  to: { agent: string; person: string };
  kind: PeerKind;
  hop: number;
  inReplyTo?: string | null;
  text: string;
  refs: PeerRef[];
  ts: number;
};

export type PeerProposal = {
  id: string;
  /** The peer message that caused it. */
  msg: string;
  case: string;
  lane: string;
  owner?: string | null;
  status: string;
  by: string;
};

export type PeerThreadWire = {
  thread: PeerThread;
  messages: PeerMessage[];
  proposals: PeerProposal[];
};

type PeerNode = {
  threads?: Record<string, Partial<PeerThread> & { id: string }>;
  prefs?: Record<string, Partial<PeerPrefs>>;
  causes?: Record<string, { msg: string; from: string; thread: string; ts: number }>;
  room?: { external?: boolean };
};

function nodeOf(doc: RoomDoc | null): PeerNode {
  return ((doc as (RoomDoc & { peers?: PeerNode }) | null)?.peers ?? {}) as PeerNode;
}

function normalise(t: Partial<PeerThread> & { id: string }): PeerThread {
  const participants = t.participants ?? [];
  return {
    id: t.id,
    participants,
    agents: t.agents ?? participants.map((p) => `${p}.agent`),
    opener: t.opener ?? participants[0] ?? "",
    count: t.count ?? 0,
    lastTs: t.lastTs ?? 0,
    status: t.status ?? "open",
    hop: t.hop ?? 0,
    maxHops: t.maxHops ?? MAX_HOPS,
    maxMessages: t.maxMessages ?? MAX_MESSAGES,
    ...(t.pending ? { pending: t.pending } : {}),
    ...(t.notice ? { notice: t.notice } : {}),
  };
}

/** Every thread in the room document, newest activity first. */
export function peerThreadsOf(doc: RoomDoc | null): PeerThread[] {
  const threads = Object.values(nodeOf(doc).threads ?? {}).map(normalise);
  return threads.sort((a, b) => b.lastTs - a.lastTs);
}

export function prefsOf(doc: RoomDoc | null, person: string): PeerPrefs {
  return { ...DEFAULT_PREFS, ...(nodeOf(doc).prefs?.[person] ?? {}) };
}

export function externalAllowed(doc: RoomDoc | null): boolean {
  return nodeOf(doc).room?.external !== false;
}

/** The other person in a thread, from `me`'s side. With no `me` it is the second person. */
export function otherOf(t: PeerThread, me?: string | null): string {
  if (me && t.participants.includes(me)) return t.participants.find((p) => p !== me) ?? me;
  return t.participants[1] ?? t.participants[0] ?? "";
}

export function involves(t: PeerThread, person?: string | null): boolean {
  return !!person && t.participants.includes(person);
}

export type Counter = { value: number; max: number; atCap: boolean; left: number };

/** The hop counter against the 4-hop cap. */
export function hopCounter(t: PeerThread): Counter {
  const max = t.maxHops || MAX_HOPS;
  return { value: Math.min(t.hop, max), max, atCap: t.hop >= max, left: Math.max(0, max - t.hop) };
}

/** The message counter against the 12-message cap. */
export function messageCounter(t: PeerThread): Counter {
  const max = t.maxMessages || MAX_MESSAGES;
  return {
    value: Math.min(t.count, max),
    max,
    atCap: t.count >= max,
    left: Math.max(0, max - t.count),
  };
}

export type ThreadState = "replying" | "capped" | "paused" | "stopped" | "closed" | "quiet";

/** One word for where a thread stands, for the strip and the list. */
export function threadState(t: PeerThread): ThreadState {
  if (t.status === "closed") return "closed";
  if (t.pending) return "replying";
  const code = t.notice?.code;
  if (code === "capped") return "capped";
  if (code === "paused") return "paused";
  if (code === "undeliverable" || code === "declined" || code === "refused") return "stopped";
  return "quiet";
}

export function agentLabel(doc: RoomDoc | null, aid: string): string {
  if (!doc) return aid;
  const person = aid.replace(/\.agent$/, "");
  const a = agentOf(doc, person);
  return a?.brought ? (a.card?.name ?? aid) : aid;
}

export function ownerLabel(doc: RoomDoc | null, person: string): string {
  return nameOf(person, doc);
}

/** The proposals that show under one message. A proposal is caused by the message an agent
 * answered and arrives with the answer, so it shows under that answer; when the agent only
 * proposed and said nothing, it shows under the message that caused it. */
export function proposalsAfter(wire: PeerThreadWire | null, message: PeerMessage): PeerProposal[] {
  if (!wire) return [];
  const answered = wire.messages.some((m) => m.inReplyTo === message.id);
  return wire.proposals.filter((p) =>
    message.inReplyTo && p.msg === message.inReplyTo ? true : !answered && p.msg === message.id,
  );
}

// --- calls ---------------------------------------------------------------------------------

export function fetchThread(room: string, thread: string): Promise<PeerThreadWire> {
  return api
    .get<{
      thread: Partial<PeerThread> & { id: string };
      messages: PeerMessage[];
      proposals: PeerProposal[];
    }>(`/rooms/${encodeURIComponent(room)}/threads/${encodeURIComponent(thread)}`)
    .then((w) => ({ thread: normalise(w.thread), messages: w.messages, proposals: w.proposals }));
}

export function closeThread(room: string, thread: string): Promise<unknown> {
  return api.post(`/rooms/${encodeURIComponent(room)}/threads/${encodeURIComponent(thread)}/close`);
}

export function savePrefs(
  room: string,
  changes: Partial<Pick<PeerPrefs, "accepts" | "paused" | "lessons" | "muted">>,
): Promise<{ prefs: PeerPrefs }> {
  return api.post(`/rooms/${encodeURIComponent(room)}/peers/settings`, changes);
}

export type ThreadLoad =
  | { status: "loading"; wire: PeerThreadWire | null }
  | { status: "ready"; wire: PeerThreadWire }
  | { status: "error"; wire: PeerThreadWire | null; message: string };

/** The bodies of one thread. They load when the thread opens and load again whenever the
 * index in the room document moves (a new message, a closed thread), so a live exchange shows
 * up without polling. The last good copy stays on screen while a refetch runs. */
export function useThreadWire(room: string, thread: PeerThread | null): ThreadLoad {
  const [load, setLoad] = useState<ThreadLoad>({ status: "loading", wire: null });
  const id = thread?.id ?? null;
  const stamp = thread ? `${thread.count}:${thread.status}:${thread.lastTs}` : "";
  const seen = useRef<string | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `stamp` is what makes a moved index refetch
  useEffect(() => {
    if (!id) return;
    let live = true;
    if (seen.current !== id) {
      seen.current = id;
      setLoad({ status: "loading", wire: null });
    }
    fetchThread(room, id)
      .then((wire) => live && setLoad({ status: "ready", wire }))
      .catch((e: unknown) => {
        if (!live) return;
        const message = e instanceof Error ? e.message : "The thread did not load.";
        setLoad((prev) => ({ status: "error", wire: prev.wire, message }));
      });
    return () => {
      live = false;
    };
  }, [room, id, stamp]);

  return useMemo(() => load, [load]);
}

/** Which message ids are new since the last render, so only they animate in. The first render
 * of a thread shows everything at once. */
export function useFreshIds(ids: string[], key: string): Set<string> {
  const known = useRef<{ key: string; ids: Set<string> } | null>(null);
  const fresh = new Set<string>();
  if (known.current && known.current.key === key) {
    for (const id of ids) if (!known.current.ids.has(id)) fresh.add(id);
  }
  useEffect(() => {
    known.current = { key, ids: new Set(ids) };
  });
  return fresh;
}

/** "your agent talked with colleague.agent (3) · open", for the one-line strip in the chat. */
export function stripLine(t: PeerThread, me?: string | null, doc?: RoomDoc | null): string {
  const other = otherOf(t, me);
  const aid = me ? (t.agents[t.participants.indexOf(other)] ?? `${other}.agent`) : null;
  if (!me) return `${t.participants.join(", ")} · ${t.count}`;
  const label = doc ? agentLabel(doc, aid ?? `${other}.agent`) : (aid ?? `${other}.agent`);
  const state = threadState(t);
  const tail =
    state === "replying"
      ? "replying"
      : state === "capped"
        ? "capped"
        : state === "paused"
          ? "paused"
          : state === "stopped"
            ? "stopped"
            : "open";
  return `Your agent talked with ${label} (${t.count}) · ${tail}`;
}

// --- one chat for every agent (Hesham, 2026-10-22) -------------------------------------------

export type PeerAskResult = { sent: boolean; code?: string; text?: string; thread?: string };

/** A person's own words to a colleague's agent, sent as their agent (`POST /peers/ask`). A
 * refusal is a calm sentence in `text`, not an error. */
export function askAgent(
  room: string,
  to: string,
  text: string,
  kind: PeerKind = "question",
): Promise<PeerAskResult> {
  return api.post<PeerAskResult>(`/rooms/${encodeURIComponent(room)}/peers/ask`, {
    to,
    text,
    kind,
  });
}

/** One message of a thread `me` takes part in, with its thread. */
export type PeerEntry = PeerMessage & { thread: string };
export type PeerWaiting = { thread: string; agent: string };
export type MyPeerFeed = { entries: PeerEntry[]; waiting: PeerWaiting[] };

const stampOf = (t: PeerThread) => `${t.count}:${t.status}:${t.lastTs}`;

/** Every message of the threads `me` takes part in, oldest first, and which agents are replying.
 * A thread's bodies load once, and again only when its index in the room document moves: no
 * polling. */
export function useMyPeerFeed(doc: RoomDoc | null | undefined, me: string | null | undefined) {
  const room = doc?.room.id ?? "";
  const threads = useMemo(
    () => (me ? peerThreadsOf(doc ?? null).filter((t) => involves(t, me)) : []),
    [doc, me],
  );
  const key = threads.map((t) => `${t.id}=${stampOf(t)}`).join("|");
  const [wires, setWires] = useState<Record<string, PeerThreadWire>>({});
  const fetched = useRef<Record<string, string>>({});

  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` is what makes a moved index refetch
  useEffect(() => {
    if (!room) return;
    for (const t of threads) {
      const stamp = stampOf(t);
      if (fetched.current[t.id] === stamp) continue;
      fetched.current[t.id] = stamp;
      fetchThread(room, t.id).then(
        (w) => setWires((prev) => ({ ...prev, [t.id]: w })),
        () => {
          delete fetched.current[t.id]; // try again when the index moves next
        },
      );
    }
  }, [room, key]);

  return useMemo<MyPeerFeed>(() => {
    const ids = new Set(threads.map((t) => t.id));
    const entries = Object.entries(wires)
      .filter(([id]) => ids.has(id))
      .flatMap(([id, w]) => w.messages.map((m) => ({ ...m, thread: id })))
      .sort((a, b) => a.ts - b.ts || a.hop - b.hop);
    const waiting = threads.flatMap((t) =>
      t.pending ? [{ thread: t.id, agent: t.pending.agent }] : [],
    );
    return { entries, waiting };
  }, [threads, wires]);
}
