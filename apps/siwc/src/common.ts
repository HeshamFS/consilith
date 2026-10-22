import { createHash } from "node:crypto";
import type { SessionState } from "./types.js";

export const LOGIN_URL_TIMEOUT_MS = 20_000;

/** Carries the sign-in URL from the DevKit's `openBrowser` hook to the `/login` response. */
export class LoginBridge {
  #url: string | undefined;
  #waiters = new Set<(url: string) => void>();

  /** Pass as `openBrowser` to the DevKit: capture the URL instead of opening a browser. */
  readonly openBrowser = (url: string): void => {
    this.#url = url;
    for (const waiter of this.#waiters) waiter(url);
    this.#waiters.clear();
  };

  reset(): void {
    this.#url = undefined;
  }

  /** Resolves with the next published URL, or rejects after the timeout. */
  next(timeoutMs: number): Promise<string> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#waiters.delete(waiter);
        reject(new ApiError(504, "login_timeout", "The ChatGPT sign-in URL was not produced in time."));
      }, timeoutMs);
      const waiter = (url: string) => { clearTimeout(timer); resolve(url); };
      this.#waiters.add(waiter);
    });
  }
}

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
  }
}

export const sha = (value: string) => createHash("sha256").update(value).digest();
export const isSignedIn = (s: SessionState) => s.status === "connected" && s.sharing;

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const e = error as { name?: unknown; code?: unknown; message?: unknown; status?: unknown } | null;
  if (e && e.name === "ChatGPTError" && typeof e.code === "string" && typeof e.message === "string") {
    // The DevKit builds these messages itself and keeps prompts and tokens out of them.
    switch (e.code) {
      case "subscription_sharing_usage_limit_exceeded":
        return new ApiError(429, e.code, e.message);
      case "sign_in_required":
      case "reauth_required":
      case "invalid_grant":
      case "invalid_refresh_token":
      case "refresh_token_expired":
      case "refresh_token_invalidated":
      case "refresh_token_reused":
        return new ApiError(409, "not_signed_in", "Not signed in with ChatGPT. Call POST /login.");
      case "sharing_not_enabled":
        return new ApiError(409, "plan_usage_not_enabled", e.message);
      case "connection_busy":
        return new ApiError(409, "busy", e.message);
      case "invalid_request":
        return new ApiError(400, "invalid_request", e.message);
      case "cancelled":
        return new ApiError(499, "cancelled", e.message);
      default: {
        const status = typeof e.status === "number" && e.status >= 400 && e.status < 600 ? e.status : 502;
        return new ApiError(status === 429 ? 429 : 502, e.code, e.message);
      }
    }
  }
  return new ApiError(500, "internal_error", "Unexpected error in the sidecar.");
}

