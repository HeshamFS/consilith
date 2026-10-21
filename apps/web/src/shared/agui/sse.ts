import { rawFetch } from "@/shared/http/client";

/** One AG-UI run over fetch-based SSE (headers work, unlike EventSource). It goes through
 * rawFetch, so the auth header and the dev mock apply. It resolves when the stream ends; a
 * stream that ends without RUN_FINISHED or RUN_ERROR is reported as truncated, never as
 * finished. */

export type AguiEvent = { type: string; [k: string]: unknown };

export class StreamError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export type RunEnd = "finished" | "error" | "truncated";

export async function runAgui(
  path: string,
  input: Record<string, unknown>,
  onEvent: (e: AguiEvent) => void,
  signal: AbortSignal,
): Promise<RunEnd> {
  const res = await rawFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(input),
    signal,
  });
  if (!res.ok || !res.body) {
    let message = res.statusText || "The stream did not open.";
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      /* not JSON */
    }
    throw new StreamError(res.status, message);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  return consume(reader, onEvent);
}

/** Reads events until the stream ends or a terminal event arrives. RUN_ERROR and RUN_FINISHED
 * are delivered once; nothing after them is, and the reader is released at once so the caller
 * can reconnect. */
export async function consume(
  reader: ReadableStreamDefaultReader<string>,
  onEvent: (e: AguiEvent) => void,
): Promise<RunEnd> {
  const parser = sseParser();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return "truncated";
      for (const ev of parser.push(value)) {
        onEvent(ev);
        if (ev.type === "RUN_FINISHED" || ev.type === "RUN_ERROR") {
          await reader.cancel().catch(() => undefined);
          return ev.type === "RUN_FINISHED" ? "finished" : "error";
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** Splits an SSE text stream into AG-UI events. Comments (": keepalive") and non-JSON data are
 * skipped; an object without a string `type` is dropped. */
export function sseParser() {
  let buf = "";
  return {
    push(chunk: string): AguiEvent[] {
      buf += chunk;
      const out: AguiEvent[] = [];
      const sep = /\r?\n\r?\n/g;
      let start = 0;
      for (let m = sep.exec(buf); m; m = sep.exec(buf)) {
        const block = buf.slice(start, m.index);
        start = m.index + m[0].length;
        const data = block
          .split(/\r?\n/)
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).replace(/^ /, ""))
          .join("\n");
        if (!data) continue;
        try {
          const ev = JSON.parse(data) as AguiEvent;
          if (ev && typeof ev === "object" && typeof ev.type === "string") out.push(ev);
        } catch {
          /* not an event */
        }
      }
      buf = buf.slice(start);
      return out;
    },
  };
}
