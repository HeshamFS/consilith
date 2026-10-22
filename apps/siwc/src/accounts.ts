import { createHmac } from "node:crypto";
import { request } from "node:http";
import { chmodSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { isAbsolute, join, normalize, sep } from "node:path";
import type { ReplyBody } from "./app.js";
import { ApiError, isSignedIn, LOGIN_URL_TIMEOUT_MS, LoginBridge, toApiError } from "./common.js";
import type { ChatGPTClient, SessionIdentity, SessionState } from "./types.js";

export const SIGN_IN_TTL_MS = 150_000;
export const SIGN_IN_ATTEMPTS = 3;
export const SIGN_IN_WINDOW_MS = 10 * 60_000;
export const REPLY_SLOTS = 4;
const MODEL_CACHE_MS = 5 * 60_000;
const KEY_PATTERN = /^[0-9a-f]{32}$/;
const UNKNOWN = "unknown";

/** What the manager asks the DevKit side to build for one account. */
export interface ClientSpec {
  key: string;
  storageDir: string;
  /** True for the account that keeps the pre-multi directory and key label. */
  legacy: boolean;
  openBrowser: (url: string) => void;
}

export interface ManagerOptions {
  /** The bridge secret: the identity fingerprints are keyed with it. */
  secret: string;
  /** The sidecar home. Every account directory lives under it. */
  home: string;
  createClient: (spec: ClientSpec) => ChatGPTClient;
  now?: () => number;
  signInTtlMs?: number;
  replySlots?: number;
  /** Origins the sign-in URL is expected to be on, published in the capabilities. */
  authOrigins?: string[];
  /** The fixed loopback port of the DevKit's callback listener. 0 turns the paste path off. */
  callbackPort?: number;
  /** Replaces the loopback request for a pasted callback. Tests use it. */
  forwardCallback?: (port: number, search: string) => Promise<number>;
}

interface Entry {
  /** Relative to the home. "." is the legacy directory. */
  dir: string;
  legacy?: true;
  /** HMAC of the connected identity, or "unknown" when the sign-in carried neither id nor email. */
  identity?: string;
}

interface Account {
  key: string;
  entry: Entry;
  client: ChatGPTClient;
  bridge: LoginBridge;
  loginActive: boolean;
  loginUrl: string | undefined;
  /** The `state` of the published URL, and whether a pasted callback already used it. */
  loginState: string | undefined;
  callbackUsed: boolean;
  lastLoginError: { code: string; message: string } | undefined;
  modelCache: { at: number; models: { id: string; label: string }[] } | undefined;
  inFlight: boolean;
}

/** The accountKey for a person: an HMAC of the person id under the bridge secret. The room
 * computes the same value, so the bridge never needs to know who a key belongs to. */
export function accountKeyFor(secret: string, person: string): string {
  return createHmac("sha256", secret).update(`siwc-account:${person}`).digest("hex").slice(0, 32);
}

/** A stable fingerprint of a sign-in identity, or `undefined` when it carries neither id nor email. */
export function identityOf(secret: string, identity: SessionIdentity | undefined): string | undefined {
  const id = identity?.id?.trim();
  const email = identity?.email?.trim().toLowerCase();
  const subject = id ? `id:${id}` : email ? `email:${email}` : undefined;
  return subject ? createHmac("sha256", secret).update(`siwc-identity:${subject}`).digest("hex") : undefined;
}

/** The sign-in mutex. The callback port is fixed, so one sign-in can wait at a time. */
export class SignInMutex {
  #holder: string | undefined;
  #expiresAt = 0;
  readonly #attempts = new Map<string, number[]>();

  constructor(
    private readonly now: () => number,
    readonly ttlMs: number,
  ) {}

  holder(): string | undefined {
    return this.#holder;
  }

  /** The holder, but only once its time is up. */
  expired(): string | undefined {
    return this.#holder && this.now() >= this.#expiresAt ? this.#holder : undefined;
  }

  /** Take the mutex for `key`. Never reveals who holds it. */
  acquire(key: string): void {
    if (this.#holder && this.#holder !== key) {
      throw new ApiError(409, "sign_in_busy", "Someone else is signing in. Try again in a minute.");
    }
    const recent = (this.#attempts.get(key) ?? []).filter((at) => this.now() - at < SIGN_IN_WINDOW_MS);
    if (recent.length >= SIGN_IN_ATTEMPTS) {
      this.#attempts.set(key, recent);
      throw new ApiError(429, "sign_in_rate_limited", "Too many sign-in attempts. Try again in a few minutes.");
    }
    recent.push(this.now());
    this.#attempts.set(key, recent);
    this.#holder = key;
    this.#expiresAt = this.now() + this.ttlMs;
  }

  /** Only the holder releases. Returns whether it did. */
  release(key: string): boolean {
    if (this.#holder !== key) return false;
    this.#holder = undefined;
    this.#expiresAt = 0;
    return true;
  }
}

/** A counting semaphore that gives up when the caller's request is aborted. */
export class Semaphore {
  #free: number;
  #waiters: (() => void)[] = [];

  constructor(slots: number) {
    this.#free = slots;
  }

  async acquire(signal?: AbortSignal): Promise<() => void> {
    if (this.#free > 0) {
      this.#free -= 1;
      return () => this.#release();
    }
    await new Promise<void>((resolve, reject) => {
      const waiter = () => {
        signal?.removeEventListener("abort", onAbort);
        resolve();
      };
      const onAbort = () => {
        this.#waiters = this.#waiters.filter((w) => w !== waiter);
        reject(new ApiError(499, "cancelled", "The request was cancelled."));
      };
      this.#waiters.push(waiter);
      signal?.addEventListener("abort", onAbort, { once: true });
    });
    return () => this.#release();
  }

  #release(): void {
    const next = this.#waiters.shift();
    if (next) next();
    else this.#free += 1;
  }
}

/** GET the SDK's loopback listener the way the browser would have. */
function forwardToLoopback(port: number, search: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const req = request(
      {
        hostname: "127.0.0.1",
        port,
        method: "GET",
        path: `/auth/callback${search}`,
        headers: { host: `127.0.0.1:${port}` },
        timeout: 15_000,
      },
      (res) => {
        res.resume();
        resolve(res.statusCode ?? 0);
      },
    );
    req.on("timeout", () => req.destroy(new Error("callback timeout")));
    req.on("error", reject);
    req.end();
  });
}

function safeDir(dir: string): boolean {
  if (dir === ".") return true;
  const n = normalize(dir);
  return !isAbsolute(n) && !n.split(sep).includes("..");
}

/** One DevKit client, login state, model cache and in-flight flag per account. */
export class AccountManager {
  readonly #opts: ManagerOptions;
  readonly #now: () => number;
  readonly #entries = new Map<string, Entry>();
  readonly #accounts = new Map<string, Account>();
  readonly #mutex: SignInMutex;
  readonly #replies: Semaphore;
  #identityChain: Promise<unknown> = Promise.resolve();
  #timer: NodeJS.Timeout | undefined;

  constructor(options: ManagerOptions) {
    this.#opts = options;
    this.#now = options.now ?? Date.now;
    this.#mutex = new SignInMutex(this.#now, options.signInTtlMs ?? SIGN_IN_TTL_MS);
    this.#replies = new Semaphore(options.replySlots ?? REPLY_SLOTS);
    this.#load();
  }

  // accounts.json ------------------------------------------------------------------------------
  get #file(): string {
    return join(this.#opts.home, "accounts.json");
  }

  #load(): void {
    if (!existsSync(this.#file)) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(readFileSync(this.#file, "utf8"));
    } catch {
      throw new Error("accounts.json is not valid JSON. Fix or remove it before restarting.");
    }
    const accounts = (parsed as { accounts?: Record<string, Entry> } | null)?.accounts ?? {};
    for (const [key, entry] of Object.entries(accounts)) {
      if (!KEY_PATTERN.test(key) || typeof entry?.dir !== "string" || !safeDir(entry.dir)) continue;
      this.#entries.set(key, {
        dir: entry.dir,
        ...(entry.legacy ? { legacy: true as const } : {}),
        ...(typeof entry.identity === "string" ? { identity: entry.identity } : {}),
      });
    }
  }

  #save(): void {
    mkdirSync(this.#opts.home, { recursive: true, mode: 0o700 });
    const body = JSON.stringify({ version: 1, accounts: Object.fromEntries(this.#entries) }, null, 2);
    const tmp = `${this.#file}.tmp`;
    writeFileSync(tmp, body, { mode: 0o600 });
    renameSync(tmp, this.#file);
    try {
      chmodSync(this.#file, 0o600);
    } catch {
      /* best effort on Windows */
    }
  }

  // accounts -----------------------------------------------------------------------------------
  static #checkKey(key: string): void {
    if (!KEY_PATTERN.test(key)) throw new ApiError(400, "invalid_account", "The account key is not valid.");
  }

  #dirOf(entry: Entry): string {
    return entry.dir === "." ? this.#opts.home : join(this.#opts.home, entry.dir);
  }

  /** The account for `key`, built on first use. `create` registers a key that is not known yet. */
  #account(key: string, create: boolean): Account | undefined {
    AccountManager.#checkKey(key);
    const have = this.#accounts.get(key);
    if (have) return have;
    let entry = this.#entries.get(key);
    if (!entry) {
      if (!create) return undefined;
      entry = { dir: key };
      this.#entries.set(key, entry);
      mkdirSync(this.#dirOf(entry), { recursive: true, mode: 0o700 });
      this.#save();
    }
    const bridge = new LoginBridge();
    const client = this.#opts.createClient({
      key,
      storageDir: this.#dirOf(entry),
      legacy: entry.legacy === true,
      openBrowser: bridge.openBrowser,
    });
    const account: Account = {
      key,
      entry,
      client,
      bridge,
      loginActive: false,
      loginUrl: undefined,
      loginState: undefined,
      callbackUsed: false,
      lastLoginError: undefined,
      modelCache: undefined,
      inFlight: false,
    };
    this.#accounts.set(key, account);
    return account;
  }

  /** Map the person who connected before multi-account to the legacy directory and key label.
   * The room calls this with the key of the owner in its `siwc_owner` file. */
  bindLegacy(key: string): { bound: true } {
    AccountManager.#checkKey(key);
    const existing = this.#entries.get(key);
    if (existing?.legacy) return { bound: true };
    for (const entry of this.#entries.values()) {
      if (entry.legacy) {
        throw new ApiError(409, "legacy_bound", "The earlier ChatGPT sign-in already belongs to another account.");
      }
    }
    if (existing) throw new ApiError(409, "account_exists", "This account already has its own directory.");
    this.#entries.set(key, { dir: ".", legacy: true });
    this.#save();
    return { bound: true };
  }

  // status -------------------------------------------------------------------------------------
  async status(key: string) {
    const acc = this.#account(key, false);
    if (!acc) return { signedIn: false, signingIn: false };
    const session = await acc.client.getSession();
    this.#remember(acc, session);
    const error = session.error ?? acc.lastLoginError;
    return {
      signedIn: isSignedIn(session),
      signingIn: acc.loginActive,
      ...(session.identity?.email ? { account: { email: session.identity.email } } : {}),
      ...(error ? { error: { code: error.code, message: error.message } } : {}),
    };
  }

  /** Record the identity of an account that signed in before we looked (the legacy account). */
  #remember(acc: Account, session: SessionState): void {
    if (!isSignedIn(session) || acc.entry.identity || acc.loginActive) return;
    acc.entry.identity = identityOf(this.#opts.secret, session.identity) ?? UNKNOWN;
    this.#save();
  }

  // login --------------------------------------------------------------------------------------
  async login(key: string, options: { newProfile?: boolean } = {}) {
    const acc = this.#account(key, true) as Account;
    this.#expireStale();
    if (acc.loginActive && this.#mutex.holder() === key) {
      if (acc.loginUrl) return { url: acc.loginUrl };
      return { url: await acc.bridge.next(LOGIN_URL_TIMEOUT_MS) };
    }
    const session = await acc.client.getSession();
    if (isSignedIn(session)) {
      throw new ApiError(409, "already_signed_in", "Already signed in. Disconnect first to switch accounts.");
    }
    this.#mutex.acquire(key); // 409 sign_in_busy without any URL, or 429
    acc.bridge.reset();
    acc.lastLoginError = undefined;
    acc.loginActive = true;
    acc.loginUrl = undefined;
    acc.loginState = undefined;
    acc.callbackUsed = false;
    this.#arm();
    const urlPromise = acc.bridge.next(LOGIN_URL_TIMEOUT_MS);
    urlPromise.catch(() => undefined);
    const done = acc.client
      .signIn({
        reconsent: session.status === "connected" && !session.sharing,
        ...(options.newProfile ? { newProfile: true } : {}),
      })
      .then(
        async (finished) => {
          acc.modelCache = undefined;
          await this.#checkIdentity(acc, finished);
        },
        (error: unknown) => {
          if (acc.lastLoginError?.code === "sign_in_expired") return;
          const api = toApiError(error);
          acc.lastLoginError = { code: api.code, message: api.message };
        },
      )
      .finally(() => {
        acc.loginActive = false;
        acc.loginUrl = undefined;
        acc.loginState = undefined;
        this.#mutex.release(key);
        this.#disarm();
      });
    const ended = done.then(() => {
      throw new ApiError(
        502,
        acc.lastLoginError?.code ?? "sign_in_ended",
        acc.lastLoginError?.message ?? "Sign-in finished before a URL was produced.",
      );
    });
    ended.catch(() => undefined);
    try {
      acc.loginUrl = await Promise.race([urlPromise, ended]);
      acc.loginState = new URL(acc.loginUrl).searchParams.get("state") ?? undefined;
      return { url: acc.loginUrl };
    } catch (error) {
      if (error instanceof ApiError && error.code === "login_timeout") acc.client.cancelSignIn();
      throw error;
    }
  }

  /** Complete a sign-in from a pasted callback URL, for a person whose browser is not on this host.
   * The SDK's loopback listener checks `state` and PKCE itself; this only replays the query to it.
   * The URL is never logged or returned. It must carry the `state` of this account's own pending
   * sign-in, and it works once. */
  async submitCallback(key: string, callbackUrl: string): Promise<{ accepted: true }> {
    const port = this.#opts.callbackPort ?? 0;
    if (!port) throw new ApiError(404, "paste_unavailable", "Pasting the sign-in address is not available here.");
    const acc = this.#account(key, false);
    if (!acc || !acc.loginActive || this.#mutex.holder() !== key || !acc.loginState) {
      throw new ApiError(409, "no_sign_in", "There is no sign-in waiting for you. Start one first.");
    }
    let parsed: URL;
    try {
      parsed = new URL(callbackUrl);
    } catch {
      throw new ApiError(400, "invalid_callback", "That is not a sign-in address.");
    }
    const states = parsed.searchParams.getAll("state");
    if (
      parsed.pathname !== "/auth/callback" ||
      parsed.protocol !== "http:" ||
      parsed.hostname !== "127.0.0.1" ||
      states.length !== 1 ||
      states[0] !== acc.loginState ||
      parsed.search.length > 8192
    ) {
      throw new ApiError(400, "invalid_callback", "That address does not belong to your sign-in.");
    }
    if (acc.callbackUsed) throw new ApiError(409, "callback_used", "That sign-in address was already used.");
    acc.callbackUsed = true; // single use, even if the forward fails
    const forward = this.#opts.forwardCallback ?? forwardToLoopback;
    const status = await forward(port, parsed.search).catch(() => 0);
    if (status !== 200) {
      throw new ApiError(502, "callback_rejected", "The sign-in address was not accepted. Start the sign-in again.");
    }
    return { accepted: true };
  }

  /** The TTL is shorter than the DevKit's own timeout, so a stuck sign-in frees the port. */
  #arm(): void {
    this.#disarm();
    this.#timer = setTimeout(() => this.#expireStale(true), this.#mutex.ttlMs + 5);
    this.#timer.unref();
  }

  #disarm(): void {
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = undefined;
  }

  #expireStale(force = false): void {
    const holder = force ? this.#mutex.holder() : this.#mutex.expired();
    if (!holder) return;
    const acc = this.#accounts.get(holder);
    if (acc?.loginActive) {
      acc.lastLoginError = {
        code: "sign_in_expired",
        message: "The sign-in took too long and was cancelled. Try again.",
      };
      acc.client.cancelSignIn();
    }
    this.#mutex.release(holder);
  }

  // identity -----------------------------------------------------------------------------------
  /** Compare a finished sign-in with every other account. Checks run one at a time, so two
   * sign-ins that finish together cannot both pass. */
  #checkIdentity(acc: Account, finished: SessionState): Promise<void> {
    const run = async () => {
      const mine = identityOf(this.#opts.secret, finished.identity);
      let clash: "in_use" | "unknown" | undefined;
      for (const [otherKey, entry] of this.#entries) {
        if (otherKey === acc.key) continue;
        let theirs = entry.identity;
        if (!theirs) {
          const other = this.#account(otherKey, false);
          if (!other) continue;
          const session = await other.client.getSession();
          if (!isSignedIn(session)) continue;
          theirs = identityOf(this.#opts.secret, session.identity) ?? UNKNOWN;
          entry.identity = theirs;
        }
        if (mine !== undefined && theirs === mine) clash = "in_use";
        else if (theirs === UNKNOWN || mine === undefined) clash ??= "unknown";
      }
      if (!clash) {
        acc.entry.identity = mine ?? UNKNOWN;
        this.#save();
        return;
      }
      delete acc.entry.identity;
      this.#save();
      try {
        await acc.client.disconnect();
      } catch {
        /* the sign-in is refused either way; the status shows the reason */
      }
      acc.lastLoginError =
        clash === "in_use"
          ? {
              code: "identity_in_use",
              message:
                "That ChatGPT account is already connected to another person. Use a private window or sign out of ChatGPT first, then try again.",
            }
          : {
              code: "identity_unknown",
              message:
                "This ChatGPT sign-in could not be told apart from another connected account, so it was refused.",
            };
    };
    const next = this.#identityChain.then(run, run);
    this.#identityChain = next.catch(() => undefined);
    return next;
  }

  // logout, models, reply ----------------------------------------------------------------------
  async logout(key: string) {
    const acc = this.#account(key, false);
    if (!acc) return { signedIn: false };
    if (this.#mutex.holder() === key) acc.client.cancelSignIn(); // only the holder cancels
    acc.modelCache = undefined;
    acc.lastLoginError = undefined;
    try {
      await acc.client.disconnect();
      delete acc.entry.identity;
      this.#save();
      return { signedIn: false };
    } catch (error) {
      const api = toApiError(error);
      if (api.code === "revocation_failed") {
        delete acc.entry.identity;
        this.#save();
        return { signedIn: false, warning: { code: api.code, message: api.message } };
      }
      throw api;
    }
  }

  async #requireSignedIn(acc: Account): Promise<void> {
    const session = await acc.client.getSession();
    if (isSignedIn(session)) return;
    if (session.status === "connected") {
      throw new ApiError(
        409,
        "plan_usage_not_enabled",
        "Signed in, but ChatGPT plan usage is not enabled. Call POST /login to grant it.",
      );
    }
    throw new ApiError(409, "not_signed_in", "Not signed in with ChatGPT. Call POST /login.");
  }

  async models(key: string): Promise<{ id: string; label: string }[]> {
    const acc = this.#account(key, false);
    if (!acc) throw new ApiError(409, "not_signed_in", "Not signed in with ChatGPT. Call POST /login.");
    await this.#requireSignedIn(acc);
    return this.#models(acc);
  }

  async #models(acc: Account) {
    if (acc.modelCache && this.#now() - acc.modelCache.at < MODEL_CACHE_MS) return acc.modelCache.models;
    const listed = await acc.client.listModels();
    const result = listed.map((m) => ({ id: m.slug, label: m.displayName }));
    acc.modelCache = { at: this.#now(), models: result };
    return result;
  }

  async reply(key: string, body: ReplyBody, signal: AbortSignal, defaultModel?: string) {
    const acc = this.#account(key, false);
    if (!acc) throw new ApiError(409, "not_signed_in", "Not signed in with ChatGPT. Call POST /login.");
    if (acc.inFlight) {
      throw new ApiError(409, "busy", "This ChatGPT account is already answering. Wait for it to finish.");
    }
    acc.inFlight = true; // claimed before any await: one in-flight call per account
    let release: (() => void) | undefined;
    try {
      await this.#requireSignedIn(acc);
      let model = body.model ?? defaultModel;
      if (!model) {
        const first = (await this.#models(acc))[0];
        if (!first) throw new ApiError(502, "no_models", "The signed-in plan lists no usable models.");
        model = first.id;
      }
      release = await this.#replies.acquire(signal);
      const result = await acc.client.streamResponse({
        model,
        input: body.text,
        signal: AbortSignal.any([signal, AbortSignal.timeout(180_000)]),
        ...(body.system.trim() ? { instructions: body.system } : {}),
      });
      return { text: result.text, model };
    } finally {
      release?.();
      acc.inFlight = false;
    }
  }

  capabilities() {
    return {
      apiVersion: 2,
      multi: true,
      accountKey: "hmac-sha256-hex32",
      maxConcurrentReplies: this.#opts.replySlots ?? REPLY_SLOTS,
      perAccountInFlight: 1,
      signIn: {
        ttlSeconds: Math.round(this.#mutex.ttlMs / 1000),
        attemptsPer10Minutes: SIGN_IN_ATTEMPTS,
        oneAtATime: true,
        authOrigins: this.#opts.authOrigins ?? ["https://auth.openai.com"],
        pasteCallback: (this.#opts.callbackPort ?? 0) > 0,
      },
      identity: { uniqueness: "enforced", stableId: false, email: true },
      // What the A0 probe found in the pinned DevKit source; see docs/agent-loop/siwc-probe.md.
      probe: {
        devkitSource: "read",
        independentInstances: "yes",
        stableIdExposed: false,
        callbackPort: "shared-one-at-a-time",
      },
      reply: { tools: false, textFormat: false, previousResponseId: false, deltaHook: false },
    };
  }

  /** Cancel every pending sign-in and stop the TTL timer. */
  shutdown(): void {
    this.#disarm();
    for (const acc of this.#accounts.values()) if (acc.loginActive) acc.client.cancelSignIn();
  }
}
