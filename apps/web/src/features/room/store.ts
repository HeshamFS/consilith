import { useEffect, useMemo, useSyncExternalStore } from "react";
import type { A2UIMessage } from "@/shared/a2ui/types";
import type { PatchOp } from "@/shared/agui/patch";
import { type AguiEvent, runAgui, StreamError } from "@/shared/agui/sse";
import { ApiError, api, rawFetch } from "@/shared/http/client";
import { setSession } from "@/shared/http/session";
import { type FeedState, initialFeed, reduceFeed } from "./feed";
import type { AgentConfig, BroughtCard, CaseId, RoomDoc } from "./types";

/** One live room: the feed run kept open (a new run after each rotation, with backoff after a
 * failure), and the REST calls that change the room. Each run has a generation; events from an
 * older run are dropped, so a late event never lands on the fresh snapshot. */
export class RoomConnection {
  private state: FeedState = initialFeed;
  private listeners = new Set<() => void>();
  private gen = 0;
  private abort: AbortController | null = null;
  private stopped = false;
  private failures = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    readonly room: string,
    readonly person: string,
  ) {}

  get = () => this.state;

  activatePlugin = async (pluginId: string) => {
    const doc = await api.post<RoomDoc>(
      this.path(`/plugins/${encodeURIComponent(pluginId)}/activate`),
    );
    this.set({ ...this.state, doc });
  };

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  private set(next: FeedState) {
    if (next === this.state) return;
    this.state = next;
    for (const l of this.listeners) l();
  }

  start() {
    this.stopped = false;
    void this.loop();
  }

  stop() {
    this.stopped = true;
    this.gen++;
    this.abort?.abort();
    if (this.timer) clearTimeout(this.timer);
  }

  private async loop() {
    while (!this.stopped) {
      const gen = ++this.gen;
      const ctrl = new AbortController();
      this.abort = ctrl;
      if (this.state.connection !== "live")
        this.set({ ...this.state, connection: this.state.doc ? "reconnecting" : "connecting" });
      let end: "finished" | "error" | "truncated" | "refused" = "truncated";
      try {
        end = await runAgui(
          `/rooms/${encodeURIComponent(this.room)}/agui/feed`,
          {
            threadId: `${this.room}:${this.person}:feed`,
            runId: `feed-${crypto.randomUUID()}`,
            messages: [],
            state: {},
            tools: [],
            context: [],
            forwardedProps: {},
          },
          (e: AguiEvent) => {
            if (gen !== this.gen) return;
            if (e.type === "STATE_SNAPSHOT") this.failures = 0;
            if (e.type === "RUN_ERROR" && e.code === "session_ended") {
              this.stopped = true;
              setSession(null);
            }
            this.set(reduceFeed(this.state, e));
          },
          ctrl.signal,
        );
      } catch (err) {
        if (this.stopped || gen !== this.gen) return;
        if (err instanceof StreamError && [401, 403, 404].includes(err.status)) {
          this.set({ ...this.state, connection: "offline", error: err.message });
          if (err.status === 401) setSession(null);
          return;
        }
        end = "refused";
      }
      if (this.stopped || gen !== this.gen) return;
      // A rotation (RUN_FINISHED cancelled) reconnects at once; anything else backs off.
      const wait = end === "finished" ? 0 : Math.min(15_000, 1000 * 2 ** this.failures++);
      if (end !== "finished") this.set({ ...this.state, connection: "reconnecting" });
      if (wait) await new Promise<void>((r) => (this.timer = setTimeout(r, wait)));
    }
  }

  // REST calls. Each answer comes back through the feed as a STATE_DELTA; the promise only
  // reports success or the server's error.
  private path(p: string) {
    return `/rooms/${encodeURIComponent(this.room)}${p}`;
  }
  claim = (c: CaseId, lane: string) =>
    api.post<{ seq: number }>(this.path(`/lanes/${c}/${lane}/claim`));
  release = (c: CaseId, lane: string) =>
    api.post<{ seq: number }>(this.path(`/lanes/${c}/${lane}/release`));
  editTwin = (patch: PatchOp[], reason: string) =>
    api.post<{ seq: number }>(this.path("/twin"), { patch, reason, opId: crypto.randomUUID() });
  decide = (proposal: string, accept: boolean, reason: string) =>
    api.post(this.path(`/proposals/${encodeURIComponent(proposal)}/decision`), {
      accept,
      reason,
      opId: crypto.randomUUID(),
    });
  ackWarning = (id: string) => api.post(this.path(`/warnings/${encodeURIComponent(id)}/ack`));
  config = (aid: string) =>
    api.get<AgentConfig>(this.path(`/agents/${encodeURIComponent(aid)}/config`));
  setConfig = (aid: string, cfg: AgentConfig) =>
    api.put<{ seq: number }>(this.path(`/agents/${encodeURIComponent(aid)}/config`), cfg);
  bring = (aid: string, cardUrl: string) =>
    api.put<BroughtCard>(this.path(`/agents/${encodeURIComponent(aid)}/brought`), { cardUrl });
  invite = (email?: string) =>
    api.post<{ id: string; url: string; expiresAt: string }>(
      this.path("/invites"),
      email ? { email } : {},
    );
  solve = (c: CaseId) => api.post(this.path(`/solve/${c}`), {});
  intake = (body: { kind?: string; text?: string }) =>
    api.post<
      | { supported: true; case: CaseId; label: string; grammar: string }
      | { supported: false; cannot: string; question: string; closest: CaseId | null }
    >(this.path("/intake"), body);
  compose = (c: CaseId, request: string, preview = false) =>
    api.post(this.path(`/surfaces/${encodeURIComponent(c)}/compose`), { request, preview });
  surfaces = (c: CaseId, as?: string) =>
    api.get<{ viewer: string; messages: A2UIMessage[] }>(
      this.path(`/surfaces/${c}${as ? `?as=${encodeURIComponent(as)}` : ""}`),
    );
  memory = () =>
    api.get<{
      personal: import("./types").Lesson[];
      room: Record<string, import("./types").Lesson>;
    }>(this.path("/memory"));

  /** Content-addressed upload: the raw bytes, one of the media types the room accepts. */
  upload = async (blob: Blob, mediaType: string) => {
    const res = await rawFetch(this.path("/artifacts"), {
      method: "POST",
      headers: { "Content-Type": mediaType },
      body: blob,
    });
    const data = (await res.json().catch(() => null)) as
      | { sha256: string; mediaType: string; bytes: number }
      | { error?: { code?: string; message?: string } }
      | null;
    if (!res.ok || !data || !("sha256" in data)) {
      const e = data && "error" in data ? data.error : undefined;
      throw new ApiError(res.status, e?.code ?? "error", e?.message ?? "The file was not stored.");
    }
    return data;
  };

  private lastPresence = 0;
  presence = (p: { cursor?: number[]; focus?: string; case?: string }) => {
    const now = Date.now();
    if (now - this.lastPresence < 250) return;
    this.lastPresence = now;
    void api.post(this.path("/presence"), p).catch(() => undefined);
  };
}

/** The connection for this room and person, started on mount and stopped on unmount. */
export function useRoom(room: string, person: string) {
  const conn = useMemo(() => new RoomConnection(room, person), [room, person]);
  useEffect(() => {
    conn.start();
    return () => conn.stop();
  }, [conn]);
  const state = useSyncExternalStore(conn.subscribe, conn.get, conn.get);
  return { conn, state };
}
