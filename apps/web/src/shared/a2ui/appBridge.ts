/** The host half of the MCP Apps bridge (JSON-RPC 2.0 over postMessage) for a plugin app frame.
 *
 * A plugin app is untrusted code in a sandboxed frame. The bridge lets it do three things:
 * start up (`ui/initialize`), receive the data of its artifact and the host's theme, and say how
 * tall it wants to be. It can never call a tool, write to the chat, open a link or read a
 * resource: every other request is answered with an error. Messages count only when they come
 * from the frame's own window, and replies go only to that window. Nothing from a message
 * payload is logged. */

export const PROTOCOL_VERSION = "2026-01-26";
export const DEFAULT_HEIGHT = 420;
export const MIN_HEIGHT = 240;
export const MAX_HEIGHT = 900;
export const NOT_ALLOWED = "not allowed in Consilith";

/** JSON-RPC "method not found": the host does not offer that method to a plugin app. */
const REFUSED = -32601;

/** What Consilith tells a plugin app about the place it is shown in. `viewport` means the page
 * owns the title, tables and actions, and the app draws only its own view; `theme` follows the
 * page, as `HostContext.theme` does. */
export type ConsilithContext = { layout: "viewport"; theme: "light" | "dark" };

export type HostContext = {
  theme: "light" | "dark";
  displayMode: "inline";
  availableDisplayModes: ["inline"];
  containerDimensions: { width: number; height: number };
  locale: string;
  platform: "web";
  consilith?: ConsilithContext;
};

/** The context as the app gets it: always in viewport layout, with the theme of the page. */
export function appContext(context: HostContext): HostContext {
  return { ...context, consilith: { layout: "viewport", theme: context.theme } };
}

type Message = {
  jsonrpc?: unknown;
  id?: unknown;
  method?: unknown;
  params?: unknown;
};

export type BridgeOptions = {
  /** The frame's window, or null before it exists. The only window ever read from or written to. */
  frame: () => Window | null;
  /** The host's context right now. */
  context: () => HostContext;
  /** A new height the app asked for, already clamped. */
  onSize?: (height: number) => void;
  /** The app finished starting up. */
  onReady?: () => void;
};

export type AppBridge = {
  /** Feed every `message` event of the page here. */
  handle: (event: Pick<MessageEvent, "source" | "data">) => void;
  /** Hand the app its data. Sent at once if the app is ready, else when it becomes ready. */
  setResult: (data: unknown) => void;
  /** Tell the app the host context changed (theme, size). */
  contextChanged: () => void;
  /** A new page is loading in the frame: it must start up again before it is sent anything. */
  reset: () => void;
  readonly ready: boolean;
};

/** A requested height held between the smallest and largest an app may take; null when it is not
 * a usable number. */
export function clampHeight(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.round(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, value)));
}

function isMessage(data: unknown): data is Message {
  return !!data && typeof data === "object" && (data as Message).jsonrpc === "2.0";
}

export function createAppBridge(options: BridgeOptions): AppBridge {
  let ready = false;
  let hasResult = false;
  let result: unknown;

  const post = (message: Record<string, unknown>) => {
    const target = options.frame();
    // A sandboxed frame has an opaque origin, so "*" is the only target that reaches it.
    if (target) target.postMessage({ jsonrpc: "2.0", ...message }, "*");
  };
  const reply = (id: unknown, body: { result: unknown } | { error: unknown }) =>
    post({ id, ...body });
  const refuse = (id: unknown) => reply(id, { error: { code: REFUSED, message: NOT_ALLOWED } });

  const sendResult = () => {
    if (!hasResult) return;
    const structured =
      result && typeof result === "object" && !Array.isArray(result) ? result : { value: result };
    post({
      method: "ui/notifications/tool-result",
      params: {
        content: [{ type: "text", text: "Stored artifact data" }],
        structuredContent: structured,
      },
    });
  };

  return {
    get ready() {
      return ready;
    },
    handle(event) {
      const frame = options.frame();
      if (!frame || event.source !== frame) return;
      const m = event.data;
      if (!isMessage(m)) return;
      const hasId = m.id !== undefined && m.id !== null;
      if (typeof m.method !== "string") return; // a response to nothing we asked
      if (hasId) {
        if (m.method === "ui/initialize") {
          const asked = (m.params as { protocolVersion?: unknown } | null)?.protocolVersion;
          reply(m.id, {
            result: {
              protocolVersion: typeof asked === "string" && asked ? asked : PROTOCOL_VERSION,
              hostInfo: { name: "Consilith", version: "1" },
              hostCapabilities: {},
              hostContext: appContext(options.context()),
            },
          });
        } else {
          refuse(m.id);
        }
        return;
      }
      if (m.method === "ui/notifications/initialized") {
        ready = true;
        options.onReady?.();
        sendResult();
      } else if (m.method === "ui/notifications/size-changed") {
        const height = clampHeight((m.params as { height?: unknown } | null)?.height);
        if (height !== null) options.onSize?.(height);
      }
      // Any other notification is ignored.
    },
    setResult(data) {
      result = data;
      hasResult = true;
      if (ready) sendResult();
    },
    reset() {
      ready = false;
    },
    contextChanged() {
      if (ready)
        post({
          method: "ui/notifications/host-context-changed",
          params: appContext(options.context()),
        });
    },
  };
}
