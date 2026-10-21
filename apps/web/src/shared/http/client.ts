import { getSession, setSession } from "./session";

const MOCK = import.meta.env.DEV && import.meta.env.VITE_API_MOCK === "1";
export const usingMock = MOCK;

/** The only place the app calls fetch. Errors keep the server's code and message. */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export const API = "/api";

export function authHeaders(): Record<string, string> {
  const s = getSession();
  return s ? { Authorization: `Bearer ${s.token}` } : {};
}

export async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  for (const [k, v] of Object.entries(authHeaders())) headers.set(k, v);
  const req = { ...init, headers };
  if (MOCK) {
    // Loaded lazily so a production bundle never contains it.
    const { mockFetch } = await import("./mock");
    return mockFetch(`${API}${path}`, req);
  }
  return fetch(`${API}${path}`, req);
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  let res: Response;
  try {
    res = await rawFetch(path, init);
  } catch {
    throw new ApiError(0, "offline", "The room server is not reachable.");
  }
  if (res.status === 401) setSession(null);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(
      res.status,
      "bad_response",
      "The server answered with something unreadable.",
    );
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? "error", err?.message ?? res.statusText);
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};
