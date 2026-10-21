import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { ChatGPTClient, SessionState } from "./types.js";

export const SECRET_HEADER = "x-consilith-siwc";
const MAX_BODY_BYTES = 8 * 1024 * 1024;
const LOGIN_URL_TIMEOUT_MS = 20_000;
const MODEL_CACHE_MS = 5 * 60_000;

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

export interface AppOptions {
  secret: string;
  client: ChatGPTClient;
  bridge: LoginBridge;
  defaultModel?: string;
  /** Optional authenticated lifecycle hook for the managed desktop helper. */
  shutdown?: () => void;
  /** Called with method, path (no query) and status only. */
  log?: (line: string) => void;
}

const sha = (value: string) => createHash("sha256").update(value).digest();
const isSignedIn = (s: SessionState) => s.status === "connected" && s.sharing;

function toApiError(error: unknown): ApiError {
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

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) throw new ApiError(413, "body_too_large", "Request body is too large.");
    chunks.push(chunk as Buffer);
  }
  let parsed: unknown;
  try { parsed = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new ApiError(400, "invalid_json", "Request body must be JSON."); }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new ApiError(400, "invalid_json", "Request body must be a JSON object.");
  }
  return parsed as Record<string, unknown>;
}

export function createApp(options: AppOptions): Server {
  const { client, bridge } = options;
  const expected = sha(options.secret);
  const log = options.log ?? ((line: string) => console.log(line));
  let loginActive = false;
  let loginUrl: string | undefined;
  let lastLoginError: { code: string; message: string } | undefined;
  let modelCache: { at: number; models: { id: string; label: string }[] } | undefined;

  async function requireSignedIn(): Promise<void> {
    const session = await client.getSession();
    if (isSignedIn(session)) return;
    if (session.status === "connected") {
      throw new ApiError(409, "plan_usage_not_enabled", "Signed in, but ChatGPT plan usage is not enabled. Call POST /login to grant it.");
    }
    throw new ApiError(409, "not_signed_in", "Not signed in with ChatGPT. Call POST /login.");
  }

  async function models() {
    if (modelCache && Date.now() - modelCache.at < MODEL_CACHE_MS) return modelCache.models;
    const listed = await client.listModels();
    const result = listed.map((m) => ({ id: m.slug, label: m.displayName }));
    modelCache = { at: Date.now(), models: result };
    return result;
  }

  async function status() {
    const session = await client.getSession();
    const error = session.error ?? lastLoginError;
    return {
      signedIn: isSignedIn(session),
      signingIn: loginActive,
      ...(session.identity?.email ? { account: { email: session.identity.email } } : {}),
      ...(error ? { error: { code: error.code, message: error.message } } : {}),
    };
  }

  async function login() {
    if (loginActive && loginUrl) return { url: loginUrl };
    if (loginActive) return { url: await bridge.next(LOGIN_URL_TIMEOUT_MS) };
    const session = await client.getSession();
    if (isSignedIn(session)) throw new ApiError(409, "already_signed_in", "Already signed in. Call POST /logout first to switch accounts.");
    bridge.reset();
    lastLoginError = undefined;
    loginActive = true;
    loginUrl = undefined;
    const urlPromise = bridge.next(LOGIN_URL_TIMEOUT_MS);
    urlPromise.catch(() => undefined);
    const done = client.signIn({ reconsent: session.status === "connected" && !session.sharing }).then(
      () => { modelCache = undefined; },
      (error: unknown) => {
        const api = toApiError(error);
        lastLoginError = { code: api.code, message: api.message };
      },
    ).finally(() => { loginActive = false; loginUrl = undefined; });
    const ended = done.then(() => {
      throw new ApiError(502, lastLoginError?.code ?? "sign_in_ended", lastLoginError?.message ?? "Sign-in finished before a URL was produced.");
    });
    ended.catch(() => undefined);
    try {
      loginUrl = await Promise.race([urlPromise, ended]);
      return { url: loginUrl };
    } catch (error) {
      if (error instanceof ApiError && error.code === "login_timeout") client.cancelSignIn();
      throw error;
    }
  }

  async function logout() {
    client.cancelSignIn();
    modelCache = undefined;
    lastLoginError = undefined;
    try {
      await client.disconnect();
      return { signedIn: false };
    } catch (error) {
      const api = toApiError(error);
      if (api.code === "revocation_failed") {
        return { signedIn: false, warning: { code: api.code, message: api.message } };
      }
      throw api;
    }
  }

  async function reply(req: IncomingMessage, signal: AbortSignal) {
    const body = await readJson(req);
    if (typeof body.system !== "string") throw new ApiError(400, "invalid_request", "`system` must be a string.");
    if (typeof body.text !== "string" || !body.text.trim()) throw new ApiError(400, "invalid_request", "`text` must be a non-empty string.");
    if (body.model !== undefined && (typeof body.model !== "string" || !body.model.trim())) {
      throw new ApiError(400, "invalid_request", "`model` must be a non-empty string when given.");
    }
    await requireSignedIn();
    let model = typeof body.model === "string" ? body.model.trim() : options.defaultModel;
    if (!model) {
      const first = (await models())[0];
      if (!first) throw new ApiError(502, "no_models", "The signed-in plan lists no usable models.");
      model = first.id;
    }
    const result = await client.streamResponse({
      model,
      input: body.text,
      signal: AbortSignal.any([signal, AbortSignal.timeout(180_000)]),
      ...(body.system.trim() ? { instructions: body.system } : {}),
    });
    return { text: result.text, model };
  }

  async function route(req: IncomingMessage, signal: AbortSignal): Promise<{ status: number; body: unknown }> {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const key = `${req.method} ${url.pathname}`;
    switch (key) {
      case "POST /shutdown": {
        if (!options.shutdown) throw new ApiError(404, "not_found", "Unknown path.");
        setTimeout(options.shutdown, 50);
        return { status: 200, body: { stopping: true } };
      }
      case "GET /status": return { status: 200, body: await status() };
      case "POST /login": return { status: 200, body: await login() };
      case "POST /logout": return { status: 200, body: await logout() };
      case "GET /models": {
        await requireSignedIn();
        return { status: 200, body: { models: await models(), source: "plan" } };
      }
      case "POST /reply": return { status: 200, body: await reply(req, signal) };
    }
    if (["/status", "/login", "/logout", "/models", "/reply"].includes(url.pathname)) {
      throw new ApiError(405, "method_not_allowed", "Method not allowed for this path.");
    }
    throw new ApiError(404, "not_found", "Unknown path.");
  }

  function send(res: ServerResponse, status: number, body: unknown): void {
    const text = JSON.stringify(body);
    res.writeHead(status, {
      "content-type": "application/json; charset=utf-8",
      "content-length": Buffer.byteLength(text),
      "cache-control": "no-store",
    });
    res.end(text);
  }

  return createServer((req, res) => {
    const controller = new AbortController();
    res.on("close", () => { if (!res.writableEnded) controller.abort(); });
    const method = req.method ?? "?";
    const path = (req.url ?? "/").split("?", 1)[0] ?? "/";
    const finish = (status: number, body: unknown) => {
      send(res, status, body);
      log(`${method} ${path} ${status}`);
    };
    const provided = req.headers[SECRET_HEADER];
    const ok = typeof provided === "string" && timingSafeEqual(sha(provided), expected);
    if (!ok) {
      req.resume();
      finish(403, { error: { code: "forbidden", message: "Missing or wrong X-Consilith-Siwc header." } });
      return;
    }
    route(req, controller.signal).then(
      ({ status, body }) => finish(status, body),
      (error: unknown) => {
        const api = toApiError(error);
        finish(api.status, { error: { code: api.code, message: api.message } });
      },
    );
  });
}
