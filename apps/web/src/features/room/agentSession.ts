import { useCallback, useEffect, useRef, useState } from "react";
import { type AguiEvent, runAgui, StreamError } from "@/shared/agui/sse";
import type { Interrupt } from "./types";

export type ChatMessage = { id: string; role: "user" | "assistant"; content: string };

export type SessionState = {
  messages: ChatMessage[];
  interrupts: Interrupt[];
  running: boolean;
  error: string | null;
};

/** Folds one agent-session run into the chat: text messages, the run's outcome (open
 * interrupts) and its error. Pure, so it is tested without a server. */
export function reduceSession(s: SessionState, e: AguiEvent): SessionState {
  switch (e.type) {
    case "RUN_STARTED":
      return { ...s, running: true, error: null };
    case "TEXT_MESSAGE_START":
      return {
        ...s,
        messages: [...s.messages, { id: String(e.messageId), role: "assistant", content: "" }],
      };
    case "TEXT_MESSAGE_CONTENT": {
      const id = String(e.messageId);
      return {
        ...s,
        messages: s.messages.map((m) =>
          m.id === id ? { ...m, content: m.content + String(e.delta ?? "") } : m,
        ),
      };
    }
    case "RUN_FINISHED": {
      const o = (e.outcome ?? {}) as { type?: string; interrupts?: Interrupt[] };
      return {
        ...s,
        running: false,
        interrupts: o.type === "interrupt" ? (o.interrupts ?? []) : [],
      };
    }
    case "RUN_ERROR":
      return { ...s, running: false, error: String(e.message ?? "The agent run failed.") };
    default:
      return s;
  }
}

/** Your personal agent's session (thread `{room}:{person}:agent`). Each send is one run with
 * the whole history; the server answers only new user messages. A run with no message is how
 * open approvals are discovered; a resume must answer every open interrupt. */
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
      setState((s) => ({ ...s, messages, running: true, error: null }));
      try {
        const end = await runAgui(
          `/rooms/${encodeURIComponent(room)}/agui/agent`,
          {
            threadId: `${room}:${person}:agent`,
            runId: `agent-${crypto.randomUUID()}`,
            messages: messages
              .filter((m) => m.role === "user")
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

  useEffect(() => {
    void run([]);
    return () => abort.current?.abort();
  }, [run]);

  const send = useCallback(
    (text: string) => {
      const m: ChatMessage = { id: `m-${crypto.randomUUID()}`, role: "user", content: text };
      return run([...ref.current.messages, m]);
    },
    [run],
  );

  const answer = useCallback(
    (decisions: Record<string, { approved: boolean; reason: string } | "later">) => {
      const resume = ref.current.interrupts.map((i) => {
        const d = decisions[i.id];
        return d && d !== "later"
          ? { interruptId: i.id, status: "resolved", payload: d }
          : { interruptId: i.id, status: "cancelled" };
      });
      return run(ref.current.messages, resume);
    },
    [run],
  );

  return { ...state, send, answer, refresh: () => run(ref.current.messages) };
}
