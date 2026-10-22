import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AccountManager } from "./accounts.js";
import { ApiError, isSignedIn, LOGIN_URL_TIMEOUT_MS, LoginBridge, sha, toApiError } from "./common.js";
import type { ChatGPTClient } from "./types.js";

export { ApiError, LoginBridge };

export const SECRET_HEADER = "x-consilith-siwc";
const MAX_BODY_BYTES = 8 * 1024 * 1024;
const MODEL_CACHE_MS = 5 * 60_000;
const ACCOUNT_ROUTE = /^\/v2\/accounts\/([^/]+)\/(status|login|logout|models|reply|bind-legacy|callback)$/;
const LEGACY_PATHS = ["/status", "/login", "/logout", "/models", "/reply"];

export interface AppOptions {
  secret: string;
  /** The single-account client and its login bridge. Used when `multi` is not given. */
  client?: ChatGPTClient;
  bridge?: LoginBridge;
  /** The account map. Given only when the operator turned `CONSILITH_SIWC_MULTI` on. */
  multi?: AccountManager;
  defaultModel?: string;
  /** Optional authenticated lifecycle hook for the managed desktop helper. */
  shutdown?: () => void;
  /** Called with method, path (no query) and status only. */
  log?: (line: string) => void;
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

export interface ReplyBody {
  system: string;
  text: string;
  model?: string;
}

async function readOptionalJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const length = Number(req.headers["content-length"] ?? 0);
  if (!length && !req.headers["transfer-encoding"]) {
    req.resume();
    return {};
  }
  return readJson(req);
}

export function createApp(options: AppOptions): Server {
  const { multi } = options;
  const client = options.client as ChatGPTClient;
  const bridge = options.bridge as LoginBridge;
  if (!multi && (!options.client || !options.bridge)) {
    throw new Error("createApp needs a client and a login bridge, or an account manager.");
  }
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

  async function parseReply(req: IncomingMessage): Promise<ReplyBody> {
    const body = await readJson(req);
    if (typeof body.system !== "string") throw new ApiError(400, "invalid_request", "`system` must be a string.");
    if (typeof body.text !== "string" || !body.text.trim()) throw new ApiError(400, "invalid_request", "`text` must be a non-empty string.");
    if (body.model !== undefined && (typeof body.model !== "string" || !body.model.trim())) {
      throw new ApiError(400, "invalid_request", "`model` must be a non-empty string when given.");
    }
    return { system: body.system, text: body.text, ...(typeof body.model === "string" ? { model: body.model.trim() } : {}) };
  }

  async function reply(req: IncomingMessage, signal: AbortSignal) {
    const body = await parseReply(req);
    await requireSignedIn();
    let model = body.model ?? options.defaultModel;
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
    if (key === "GET /v2/capabilities") {
      return { status: 200, body: multi ? multi.capabilities() : { apiVersion: 2, multi: false } };
    }
    const account = ACCOUNT_ROUTE.exec(url.pathname);
    if (account) {
      if (!multi) throw new ApiError(404, "multi_disabled", "Several ChatGPT accounts are not enabled on this bridge.");
      const [, accountKey, action] = account as unknown as [string, string, string];
      const wanted = action === "status" || action === "models" ? "GET" : "POST";
      if (req.method !== wanted) throw new ApiError(405, "method_not_allowed", "Method not allowed for this path.");
      switch (action) {
        case "status": return { status: 200, body: await multi.status(accountKey) };
        case "login": {
          const body = await readOptionalJson(req);
          return { status: 200, body: await multi.login(accountKey, { newProfile: body.newProfile === true }) };
        }
        case "callback": {
          const body = await readJson(req);
          if (typeof body.callbackUrl !== "string" || !body.callbackUrl) {
            throw new ApiError(400, "invalid_request", "`callbackUrl` must be a string.");
          }
          return { status: 200, body: await multi.submitCallback(accountKey, body.callbackUrl) };
        }
        case "logout": return { status: 200, body: await multi.logout(accountKey) };
        case "bind-legacy": return { status: 200, body: await multi.bindLegacy(accountKey) };
        case "models": return { status: 200, body: { models: await multi.models(accountKey), source: "plan" } };
        default: return { status: 200, body: await multi.reply(accountKey, await parseReply(req), signal, options.defaultModel) };
      }
    }
    if (multi && key !== "POST /shutdown" && LEGACY_PATHS.includes(url.pathname)) {
      throw new ApiError(404, "legacy_routes_disabled", "Use /v2/accounts/:key/... while several accounts are enabled.");
    }
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
    if (LEGACY_PATHS.includes(url.pathname)) {
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
