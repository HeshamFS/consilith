/** The person token for this tab: memory first, sessionStorage so a reload keeps the tab signed
 * in. Never localStorage: closing the tab ends the browser's copy. */

const KEY = "consilith.session";

export type Session = { token: string; sid: string };

let current: Session | null = load();
const listeners = new Set<() => void>();

function load(): Session | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const v: unknown = JSON.parse(raw);
    if (v && typeof v === "object" && "token" in v && "sid" in v) {
      const { token, sid } = v as { token: unknown; sid: unknown };
      if (typeof token === "string" && typeof sid === "string") return { token, sid };
    }
    return null;
  } catch {
    return null;
  }
}

export function getSession(): Session | null {
  return current;
}

export function setSession(s: Session | null): void {
  current = s;
  try {
    if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the session lives in memory for this page */
  }
  for (const l of listeners) l();
}

export function subscribeSession(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
