import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API, rawFetch } from "@/shared/http/client";
import { Button } from "@/shared/ui";
import s from "./app-frame.module.css";
import {
  type AppBridge,
  createAppBridge,
  DEFAULT_HEIGHT,
  type HostContext,
  MAX_HEIGHT,
  MIN_HEIGHT,
} from "./appBridge";

/** A plugin app in the page: a sandboxed frame that can show its artifact's data and nothing else.
 *
 * The frame is `<iframe sandbox="allow-scripts">` (no same-origin, forms, popups or navigation)
 * loaded from the room's app route, never from `srcdoc` or a blob, so the page's own policy and
 * origin never reach it. The bridge in `appBridge.ts` is the only way in or out. */

type Theme = "light" | "dark";
type State = "link" | "loading" | "ready" | "error";

/** The theme the page shows, followed live: the person pick on <html data-theme>, else the
 * system. Read from the document so the frame works wherever the page puts it. */
function readTheme(): Theme {
  if (typeof document === "undefined") return "light";
  const picked = document.documentElement.dataset.theme;
  if (picked === "light" || picked === "dark") return picked;
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function useHostTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    const update = () => setTheme(readTheme());
    update();
    const watcher = new MutationObserver(update);
    watcher.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const query =
      typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
    query?.addEventListener("change", update);
    return () => {
      watcher.disconnect();
      query?.removeEventListener("change", update);
    };
  }, []);
  return theme;
}

/** The address the frame loads. `#viewport` tells an app that it is embedded in Consilith and
 * draws only its own view, for hosts that read it before `ui/initialize` has been answered. */
export function frameSource(url: string): string {
  return url.includes("#") ? url : `${url}#viewport`;
}

/** Ask the room for a short-lived address of the app (a frame cannot send the bearer token). */
export async function requestAppLink(sha: string): Promise<string> {
  if (!/^[a-f0-9]{64}$/.test(sha)) throw new Error("Invalid app reference");
  const room = /^\/rooms\/([^/]+)/.exec(globalThis.location?.pathname ?? "")?.[1];
  if (!room) throw new Error("This app can only open inside a room");
  const response = await rawFetch(
    `/rooms/${encodeURIComponent(decodeURIComponent(room))}/apps/${sha}/link`,
    { method: "POST" },
  );
  if (!response.ok) throw new Error("The app could not be opened");
  const body = (await response.json()) as { url?: unknown };
  if (typeof body.url !== "string" || !body.url.startsWith("/rooms/"))
    throw new Error("The app could not be opened");
  return `${API}${body.url}`;
}

export function AppFrame({
  sha,
  title,
  plugin,
  data,
  link = requestAppLink,
  fallback,
  initialHeight = DEFAULT_HEIGHT,
}: {
  /** The stored page. */
  sha: string;
  /** The app's name, shown in the header. */
  title: string;
  /** The plugin that made it, when known. */
  plugin?: string | undefined;
  /** The JSON of the artifact the app draws, sent to it as its tool result once it is ready. */
  data?: unknown;
  /** Resolves the address to load; the default asks the room. */
  link?: (sha: string) => Promise<string>;
  /** The same data as a table, behind a "Data table" toggle. */
  fallback?: ReactNode;
  /** The height before the app says how tall it wants to be. */
  initialHeight?: number;
}) {
  const theme = useHostTheme();
  const frame = useRef<HTMLIFrameElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const bridge = useRef<AppBridge | null>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<State>("link");
  const [attempt, setAttempt] = useState(0);
  const [asked, setAsked] = useState(initialHeight);
  const [wide, setWide] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [table, setTable] = useState(false);
  const [viewport, setViewport] = useState(() => globalThis.innerHeight ?? 900);

  const height = expanded
    ? Math.round(Math.min(MAX_HEIGHT, Math.max(DEFAULT_HEIGHT, viewport * 0.78)))
    : asked;

  const context = useRef<HostContext>({
    theme,
    displayMode: "inline",
    availableDisplayModes: ["inline"],
    containerDimensions: { width: 0, height },
    locale: "en",
    platform: "web",
  });
  context.current = {
    ...context.current,
    theme,
    containerDimensions: { width: wide, height },
    locale: globalThis.navigator?.language || "en",
  };

  if (bridge.current === null) {
    bridge.current = createAppBridge({
      frame: () => frame.current?.contentWindow ?? null,
      context: () => context.current,
      onSize: (h) => setAsked(h),
    });
  }

  useEffect(() => {
    const onMessage = (event: MessageEvent) => bridge.current?.handle(event);
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (data !== undefined) bridge.current?.setResult(data);
  }, [data]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: tell the app when the host changes
  useEffect(() => {
    bridge.current?.contextChanged();
  }, [theme, wide, height]);

  useEffect(() => {
    const el = stage.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setWide(Math.round(el.getBoundingClientRect().width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const on = () => setViewport(globalThis.innerHeight);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);

  useEffect(() => {
    if (!expanded) return;
    const on = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [expanded]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new attempt asks for a new address
  useEffect(() => {
    let live = true;
    setState("link");
    setSrc(null);
    link(sha)
      .then((url) => {
        if (!live) return;
        bridge.current?.reset();
        setSrc(frameSource(url));
        setState("loading");
      })
      .catch(() => {
        if (live) setState("error");
      });
    return () => {
      live = false;
    };
  }, [sha, link, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  const name = useMemo(() => title.replaceAll("_", " "), [title]);

  return (
    <section className={s.frame} aria-label={name} data-expanded={expanded} data-state={state}>
      <header className={s.head}>
        <div className={s.who}>
          <span className={s.name}>{name}</span>
          <span className={s.by}>{plugin ? `${plugin} plugin` : "Plugin app"}</span>
        </div>
        <div className={s.actions}>
          {fallback ? (
            <Button
              variant="ghost"
              small
              aria-pressed={table}
              onClick={() => setTable((on) => !on)}
            >
              {table ? "Hide data table" : "Data table"}
            </Button>
          ) : null}
          <Button
            variant="ghost"
            small
            aria-pressed={expanded}
            onClick={() => setExpanded((on) => !on)}
          >
            {expanded ? "Close full screen" : "Open full screen"}
          </Button>
        </div>
      </header>
      <div className={s.stage} ref={stage} style={{ height }}>
        {src ? (
          <iframe
            ref={frame}
            className={s.iframe}
            title={name}
            src={src}
            sandbox="allow-scripts"
            loading="lazy"
            referrerPolicy="no-referrer"
            data-ready={state === "ready"}
            onLoad={() => setState((was) => (was === "loading" ? "ready" : was))}
          />
        ) : null}
        {state === "link" || state === "loading" ? (
          <div className={s.veil} role="status">
            <span className={s.pulse} aria-hidden="true" />
            <span>Opening {name}…</span>
          </div>
        ) : null}
        {state === "error" ? (
          <div className={s.veil} role="alert">
            <span>This app could not be opened.</span>
            <Button variant="secondary" small onClick={retry}>
              Try again
            </Button>
          </div>
        ) : null}
      </div>
      {table && fallback ? <div className={s.fallback}>{fallback}</div> : null}
    </section>
  );
}

export { MAX_HEIGHT, MIN_HEIGHT };
