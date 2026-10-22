import { useCallback, useEffect, useRef, useState } from "react";
import { type AguiEvent, runAgui, StreamError } from "@/shared/agui/sse";
import { api } from "@/shared/http/client";
import type { Interrupt } from "./types";

export type TextMessage = {
  kind: "text";
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Loaded from the server's history: never sent back as a new message. */
  hydrated?: boolean;
  /** When the server stored it, in seconds (history only), to order it among agent threads. */
  at?: number | undefined;
};

export type ToolState =
  | "running"
  | "ok"
  | "error"
  | "not_executed"
  | "awaiting_approval"
  | "cancelled";

/** A tool call. `provider` and `model` are shown in the header and never sent to a prompt. */
export type ToolCard = {
  kind: "tool";
  id: string;
  callId: string;
  tool: string;
  label: string;
  provider: string;
  model: string;
  state: ToolState;
  args: Record<string, unknown>;
  argsText: string;
  summary: string;
  resultIds: string[];
  code?: string | undefined;
  fixAction?: string | undefined;
  /** Client clock, ms: when the call began and when its result arrived (live runs only; the
   * stored history keeps no timings). */
  startedAt?: number | undefined;
  endedAt?: number | undefined;
};

export type NoticeCard = {
  kind: "notice";
  id: string;
  code: string;
  message: string;
  fixAction?: string | undefined;
};

export type ApprovalStatus = "open" | "superseded" | "approved" | "declined";

/** A confirm card in the transcript. The Approve and Decline controls sit in the approvals
 * panel; the card shows what is waiting, in the server's words. */
export type ApprovalCard = {
  kind: "approval";
  id: string;
  actionId: string;
  actionKind: string;
  message: string;
  card: Record<string, unknown>;
  /** The agent's own words, shown as untrusted. */
  note: string;
  expires: number;
  status: ApprovalStatus;
};

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export type JobCard = {
  kind: "job";
  id: string;
  jobId: string;
  title: string;
  status: JobStatus;
  message: string;
  progress?: number | undefined;
};

/** A private surface the agent showed in the chat (the A2UI messages of a `ui.show`, `ui.chart`
 * or `ui.table`). It arrives live as an `a2ui-surface` activity and comes back with the history
 * row of the call that made it, so it survives a reload. */
export type SurfaceCard = {
  kind: "surface";
  id: string;
  surfaceId: string;
  messages: unknown[];
};

export type ChatMessage =
  | TextMessage
  | ToolCard
  | NoticeCard
  | ApprovalCard
  | JobCard
  | SurfaceCard;

export type SessionState = {
  messages: ChatMessage[];
  interrupts: Interrupt[];
  running: boolean;
  error: string | null;
};

type Rec = Record<string, unknown>;

const CONFIRM_KINDS = [
  "confirm_run",
  "confirm_claim",
  "confirm_peer",
  "confirm_lesson",
  "confirm_edit",
  "confirm_pin",
];

function rec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

/** The kind of an interrupt: a confirm card (`confirm_*`, answered with `{approved}`) or a
 * proposal (answered with `{approved, reason}`, the reason required). */
export function interruptKind(i: Interrupt): string {
  const meta = rec(i.metadata);
  const kind = str(meta.kind) || str(i.reason);
  return CONFIRM_KINDS.includes(kind) ? kind : "proposal";
}

export function isConfirm(i: Interrupt): boolean {
  return interruptKind(i) !== "proposal";
}

function upsert(messages: ChatMessage[], next: ChatMessage): ChatMessage[] {
  return messages.some((m) => m.id === next.id)
    ? messages.map((m) => (m.id === next.id ? next : m))
    : [...messages, next];
}

function parseArgs(text: string): Record<string, unknown> {
  try {
    return rec(JSON.parse(text));
  } catch {
    return {};
  }
}

function toolState(v: unknown): ToolState {
  const s = str(v);
  return s === "error" ||
    s === "not_executed" ||
    s === "awaiting_approval" ||
    s === "cancelled" ||
    s === "running"
    ? s
    : "ok";
}

function approvalFromInterrupt(i: Interrupt): ApprovalCard {
  const meta = rec(i.metadata);
  return {
    kind: "approval",
    id: `approval-${i.id}`,
    actionId: str(meta.actionId, i.id),
    actionKind: interruptKind(i),
    message: i.message,
    card: rec(meta.card),
    note: str(meta.note),
    expires: typeof meta.expires === "number" ? meta.expires : 0,
    status: "open",
  };
}

function surfaceCard(surfaceId: string, messages: unknown): SurfaceCard | null {
  // An empty message list says nothing about a surface; it must never blank one that was drawn.
  if (!surfaceId || !Array.isArray(messages) || !messages.length) return null;
  return { kind: "surface", id: `surface-${surfaceId}`, surfaceId, messages };
}

/** Folds one agent-session run into the chat: text, tool cards, notices, approvals, jobs, the
 * run's outcome (open interrupts) and its error. Pure, so it is tested without a server. */
export function reduceSession(s: SessionState, e: AguiEvent): SessionState {
  switch (e.type) {
    case "RUN_STARTED":
      return { ...s, running: true, error: null };
    case "TEXT_MESSAGE_START":
      // A reply the server replays has the id it had: it replaces that message, never repeats it.
      return {
        ...s,
        messages: upsert(s.messages, {
          kind: "text",
          id: String(e.messageId),
          role: "assistant",
          content: "",
        }),
      };
    case "TEXT_MESSAGE_CONTENT": {
      const id = String(e.messageId);
      return {
        ...s,
        messages: s.messages.map((m) =>
          m.kind === "text" && m.id === id
            ? { ...m, content: m.content + String(e.delta ?? "") }
            : m,
        ),
      };
    }
    case "TOOL_CALL_START": {
      const meta = rec(e.metadata);
      const callId = String(e.toolCallId);
      const tool = String(e.toolCallName ?? "");
      return {
        ...s,
        messages: upsert(s.messages, {
          kind: "tool",
          id: `tool-${callId}`,
          callId,
          tool,
          label: str(meta.label, tool),
          provider: str(meta.provider),
          model: str(meta.model),
          state: "running",
          args: {},
          argsText: "",
          summary: "",
          resultIds: [],
          startedAt: Date.now(),
        }),
      };
    }
    case "TOOL_CALL_ARGS": {
      const id = `tool-${String(e.toolCallId)}`;
      return {
        ...s,
        messages: s.messages.map((m) =>
          m.kind === "tool" && m.id === id
            ? { ...m, argsText: m.argsText + String(e.delta ?? "") }
            : m,
        ),
      };
    }
    case "TOOL_CALL_END": {
      const id = `tool-${String(e.toolCallId)}`;
      return {
        ...s,
        messages: s.messages.map((m) =>
          m.kind === "tool" && m.id === id ? { ...m, args: parseArgs(m.argsText) } : m,
        ),
      };
    }
    case "TOOL_CALL_RESULT": {
      const id = `tool-${String(e.toolCallId)}`;
      let body: Rec;
      try {
        body = rec(JSON.parse(String(e.content ?? "")));
      } catch {
        body = { summary: String(e.content ?? "") };
      }
      const ids = Array.isArray(body.resultIds) ? body.resultIds.map(String) : [];
      return {
        ...s,
        messages: s.messages.map((m) =>
          m.kind === "tool" && m.id === id
            ? {
                ...m,
                state: toolState(body.status),
                summary: str(body.summary),
                resultIds: ids,
                code: str(body.code) || undefined,
                fixAction: str(body.fixAction) || undefined,
                endedAt: Date.now(),
              }
            : m,
        ),
      };
    }
    case "ACTIVITY_SNAPSHOT": {
      if (e.activityType !== "a2ui-surface") return s;
      const content = rec(e.content);
      const card = surfaceCard(str(content.surfaceId), content.messages);
      return card ? { ...s, messages: upsert(s.messages, card) } : s;
    }
    case "CUSTOM": {
      const value = rec(e.value);
      if (e.name === "consilith.notice") {
        const n: NoticeCard = {
          kind: "notice",
          id: `notice-${s.messages.length}`,
          code: str(value.code, "notice"),
          message: str(value.message),
          fixAction: str(value.fixAction) || undefined,
        };
        return { ...s, messages: [...s.messages, n] };
      }
      if (e.name === "consilith.job") {
        const jobId = str(value.jobId);
        if (!jobId) return s;
        const job: JobCard = {
          kind: "job",
          id: `job-${jobId}`,
          jobId,
          title: str(value.title, jobId),
          status: (str(value.status, "queued") as JobStatus) || "queued",
          message: str(value.message),
          progress: typeof value.progress === "number" ? value.progress : undefined,
        };
        return { ...s, messages: upsert(s.messages, job) };
      }
      if (e.name === "consilith.approval") {
        const actionId = str(value.actionId);
        if (!actionId) return s;
        const card: ApprovalCard = {
          kind: "approval",
          id: `approval-${actionId}`,
          actionId,
          actionKind: str(value.kind, "confirm_run"),
          message: str(value.message),
          card: rec(value.card),
          note: str(value.note),
          expires: typeof value.expires === "number" ? value.expires : 0,
          status: "open",
        };
        // A replay of a card the person already answered must not reopen it.
        const had = s.messages.find((m) => m.kind === "approval" && m.id === card.id);
        const kept = had?.kind === "approval" && had.status !== "open" ? had.status : card.status;
        return { ...s, messages: upsert(s.messages, { ...card, status: kept }) };
      }
      return s;
    }
    case "RUN_FINISHED": {
      const o = (e.outcome ?? {}) as { type?: string; interrupts?: Interrupt[] };
      const interrupts = o.type === "interrupt" ? (o.interrupts ?? []) : [];
      let messages = s.messages;
      for (const i of interrupts) {
        if (isConfirm(i) && !messages.some((m) => m.kind === "approval" && m.actionId === i.id)) {
          messages = [...messages, approvalFromInterrupt(i)];
        }
      }
      // A confirm card that is no longer open stops saying it is waiting.
      const open = new Set(interrupts.map((i) => i.id));
      messages = messages.map((m) =>
        m.kind === "approval" && m.status === "open" && !open.has(m.actionId)
          ? { ...m, status: "superseded" }
          : m,
      );
      return { ...s, messages, running: false, interrupts };
    }
    case "RUN_ERROR":
      return { ...s, running: false, error: String(e.message ?? "The agent run failed.") };
    default:
      return s;
  }
}

/** A new message supersedes every open confirm card. Proposals stay: they block chat until
 * answered or dismissed. */
export function supersedeConfirms(s: SessionState): SessionState {
  return {
    ...s,
    interrupts: s.interrupts.filter((i) => !isConfirm(i)),
    messages: s.messages.map((m) =>
      m.kind === "approval" && m.status === "open" ? { ...m, status: "superseded" } : m,
    ),
  };
}

/** The person answered: every card they approved or declined says so at once, and stops waiting
 * in the approvals panel. "Later" keeps a card open. */
export function applyDecisions(
  s: SessionState,
  decisions: Record<string, { approved: boolean; reason?: string } | "later">,
): SessionState {
  const answered = (id: string) => {
    const d = decisions[id];
    return d && d !== "later" ? d : null;
  };
  return {
    ...s,
    interrupts: s.interrupts.filter((i) => !answered(i.id)),
    messages: s.messages.map((m) => {
      const d = m.kind === "approval" && m.status === "open" ? answered(m.actionId) : null;
      return m.kind === "approval" && d
        ? { ...m, status: d.approved ? "approved" : "declined" }
        : m;
    }),
  };
}

/** One row of `GET /rooms/{id}/agent/history`. */
export type HistoryTurn = {
  id: string;
  seq: number;
  role: "user" | "agent" | "tool" | "peer" | "system";
  status: string;
  text: string;
  meta: Rec;
  ts?: number;
};

/** The transcript the server kept, oldest first. User and agent turns become messages; a tool
 * turn becomes a card with the provider and model it recorded; a notice stored in a turn's meta
 * comes back as its card. */
export function hydrateMessages(turns: HistoryTurn[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  // A notice kept on the person's message is why that turn stopped: it follows the turn's cards.
  let late: NoticeCard | null = null;
  const flush = () => {
    if (late) out.push(late);
    late = null;
  };
  for (const t of turns) {
    const meta = rec(t.meta);
    const notice = rec(meta.notice);
    const card: NoticeCard | null = notice.message
      ? {
          kind: "notice",
          id: `notice-${t.id}`,
          code: str(notice.code, "notice"),
          message: str(notice.message),
          fixAction: str(notice.fixAction) || undefined,
        }
      : null;
    if (t.role === "user") {
      flush();
      out.push({
        kind: "text",
        id: t.id,
        role: "user",
        content: t.text,
        hydrated: true,
        ...(typeof t.ts === "number" ? { at: t.ts } : {}),
      });
      late = card;
      continue;
    }
    if (t.role === "agent") {
      if (t.text)
        out.push({ kind: "text", id: t.id, role: "assistant", content: t.text, hydrated: true });
    } else if (t.role === "tool") {
      const tool = str(meta.tool, "tool");
      const ids = Array.isArray(meta.ids) ? meta.ids.map(String) : [];
      out.push({
        kind: "tool",
        id: `tool-${t.id}`,
        callId: t.id,
        tool,
        label: str(meta.label, tool),
        provider: str(meta.provider),
        model: str(meta.model_used),
        state: toolState(meta.outcome),
        args: {},
        argsText: "",
        summary: t.text,
        resultIds: ids,
      });
      const shown = rec(meta.surface);
      const surface = surfaceCard(str(shown.surfaceId), shown.messages);
      if (surface) out.push(surface);
    }
    if (card) out.push(card);
  }
  flush();
  return out;
}

/** Your personal agent's session (thread `{room}:{person}:agent`). The transcript is hydrated
 * from the server's history, and a send carries only the messages this page created: a message
 * the server already answered is never sent again. A run with no message is how open approvals
 * are discovered; a resume must answer every open interrupt. */
export function useAgentSession(room: string, person: string) {
  const [state, setState] = useState<SessionState>({
    messages: [],
    interrupts: [],
    running: false,
    error: null,
  });
  const ref = useRef(state);
  ref.current = state;
  const abort = useRef<AbortController | null>(null);

  const run = useCallback(
    async (messages: ChatMessage[], resume?: unknown[]) => {
      abort.current?.abort();
      const ctrl = new AbortController();
      abort.current = ctrl;
      // Once a message is on its way the server has it: later runs never send it again.
      setState((s) => ({
        ...s,
        messages: messages.map((m) =>
          m.kind === "text" && m.role === "user" ? { ...m, hydrated: true } : m,
        ),
        running: true,
        error: null,
      }));
      try {
        const end = await runAgui(
          `/rooms/${encodeURIComponent(room)}/agui/agent`,
          {
            threadId: `${room}:${person}:agent`,
            runId: `agent-${crypto.randomUUID()}`,
            // A resume answers cards of a message the server already has. Sending that message
            // again would make the server answer it a second time.
            messages: resume
              ? []
              : messages
                  .filter(
                    (m): m is TextMessage => m.kind === "text" && m.role === "user" && !m.hydrated,
                  )
                  .map((m) => ({ id: m.id, role: "user", content: m.content })),
            state: {},
            tools: [],
            context: [],
            forwardedProps: {},
            ...(resume ? { resume } : {}),
          },
          (e) => setState((s) => reduceSession(s, e)),
          ctrl.signal,
        );
        if (end === "truncated" && !ctrl.signal.aborted)
          setState((s) => ({ ...s, running: false, error: "The agent stream ended early." }));
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setState((s) => ({
          ...s,
          running: false,
          error: e instanceof StreamError ? e.message : "The agent is not reachable.",
        }));
      }
    },
    [room, person],
  );

  const load = useCallback(async (): Promise<ChatMessage[]> => {
    try {
      const got = await api.get<{ turns: HistoryTurn[] }>(
        `/rooms/${encodeURIComponent(room)}/agent/history`,
      );
      return hydrateMessages(got.turns ?? []);
    } catch {
      return []; /* no history yet, or the route is not up: start with an empty transcript */
    }
  }, [room]);

  useEffect(() => {
    if (!room || !person) return;
    let live = true;
    void (async () => {
      const messages = await load();
      if (live) await run(messages);
    })();
    return () => {
      live = false;
      abort.current?.abort();
    };
  }, [run, load, room, person]);

  // A turn the server started on its own (the hand-over after a run, an answer to another agent)
  // is not streamed to this tab: the room calls this when the agent comes to rest (found live,
  // 2026-10-22: the report card waited unseen until a reload).
  const reload = useCallback(async () => {
    if (ref.current.running) return;
    const messages = await load();
    if (!ref.current.running) await run(messages);
  }, [load, run]);

  const send = useCallback(
    (text: string) => {
      const m: TextMessage = {
        kind: "text",
        id: `m-${crypto.randomUUID()}`,
        role: "user",
        content: text,
      };
      const next = supersedeConfirms(ref.current);
      setState(next);
      return run([...next.messages, m]);
    },
    [run],
  );

  const answer = useCallback(
    (decisions: Record<string, { approved: boolean; reason?: string } | "later">) => {
      const cur = ref.current;
      const resume = cur.interrupts.map((i) => {
        const d = decisions[i.id];
        return d && d !== "later"
          ? { interruptId: i.id, status: "resolved", payload: d }
          : { interruptId: i.id, status: "cancelled" };
      });
      const next = applyDecisions(cur, decisions);
      setState(next);
      return run(next.messages, resume);
    },
    [run],
  );

  return { ...state, send, answer, reload, refresh: () => run(ref.current.messages) };
}

export type AgentSession = ReturnType<typeof useAgentSession>;
