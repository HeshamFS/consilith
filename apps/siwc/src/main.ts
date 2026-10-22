import { homedir } from "node:os";
import { readFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { AccountManager } from "./accounts.js";
import { createApp, LoginBridge } from "./app.js";
import { createOsCredentialEncryption } from "./credential-encryption.js";
import { loadDevkit } from "./devkit.js";
import { createCallbackProxy } from "./callback-proxy.js";
import type { ChatGPTClient } from "./types.js";

function fail(message: string): never {
  console.error(`consilith-siwc: ${message}`);
  process.exit(1);
}

let secret = process.env.CONSILITH_SIWC_SECRET;
if (!secret && process.env.CONSILITH_SIWC_SECRET_FILE) {
  try { secret = readFileSync(process.env.CONSILITH_SIWC_SECRET_FILE, "utf8").trim(); }
  catch { fail("Cannot read CONSILITH_SIWC_SECRET_FILE."); }
}
if (!secret || !secret.trim()) {
  fail("CONSILITH_SIWC_SECRET is not set. Refusing to start without a shared secret.");
}

const port = Number(process.env.CONSILITH_SIWC_PORT ?? 8730);
if (!Number.isInteger(port) || port < 1 || port > 65535) fail("CONSILITH_SIWC_PORT must be a port number.");
const host = process.env.CONSILITH_SIWC_HOST ?? "127.0.0.1";
if (host !== "127.0.0.1" && host !== "0.0.0.0") fail("CONSILITH_SIWC_HOST must be an IPv4 bind address.");
const callbackPort = Number(process.env.CONSILITH_SIWC_CALLBACK_PORT ?? 8787);
if (!Number.isInteger(callbackPort) || callbackPort < 0 || callbackPort > 65535) {
  fail("CONSILITH_SIWC_CALLBACK_PORT must be a port number (0 picks a free one).");
}
const proxyPort = Number(process.env.CONSILITH_SIWC_CALLBACK_PROXY_PORT ?? 0);
if (!Number.isInteger(proxyPort) || proxyPort < 0 || proxyPort > 65535 ||
    (proxyPort && (!callbackPort || proxyPort === callbackPort || proxyPort === port))) {
  fail("Callback proxy requires a distinct port and a fixed SDK callback port.");
}
const home = process.env.CONSILITH_SIWC_HOME?.trim() || join(homedir(), ".consilith", "siwc");
if (!isAbsolute(home)) fail("CONSILITH_SIWC_HOME must be an absolute path.");

let devkit;
try {
  devkit = await loadDevkit();
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}

const multiOn = ["1", "true", "yes", "on"].includes((process.env.CONSILITH_SIWC_MULTI ?? "").trim().toLowerCase());
const appId = process.env.CONSILITH_SIWC_APP_ID?.trim() || "consilith-siwc";
const legacyEncryption = createOsCredentialEncryption(home);
if (!await legacyEncryption.isAvailable()) {
  fail("OS credential encryption is unavailable. Unlock the operating-system credential store and restart.");
}

// One account: today's behaviour. Several: an account map, each with its own directory and key label.
let manager: AccountManager | undefined;
let single: { client: ChatGPTClient; bridge: LoginBridge } | undefined;
if (multiOn) {
  manager = new AccountManager({
    secret,
    home,
    callbackPort: process.env.CONSILITH_SIWC_PASTE === "1" ? callbackPort : 0,
    createClient: (spec) => devkit.createChatGPT({
      appName: "Consilith",
      appId,
      redirectPort: callbackPort,
      storageDir: spec.storageDir,
      credentialEncryption: spec.legacy
        ? legacyEncryption
        : createOsCredentialEncryption(spec.storageDir, { account: spec.key }),
      openBrowser: spec.openBrowser,
    }),
    ...(process.env.CONSILITH_SIWC_AUTH_ORIGINS?.trim()
      ? { authOrigins: process.env.CONSILITH_SIWC_AUTH_ORIGINS.split(",").map((o) => o.trim()).filter(Boolean) }
      : {}),
  });
} else {
  const bridge = new LoginBridge();
  single = {
    bridge,
    client: devkit.createChatGPT({
      appName: "Consilith",
      appId,
      redirectPort: callbackPort,
      storageDir: home,
      credentialEncryption: legacyEncryption,
      openBrowser: bridge.openBrowser,
    }),
  };
}

const server = createApp({
  secret,
  ...(single ?? {}),
  ...(manager ? { multi: manager } : {}),
  ...(process.env.CONSILITH_SIWC_MANAGED === "1" ? { shutdown: () => {
    single?.client.cancelSignIn();
    manager?.shutdown();
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 1000).unref();
  } } : {}),
  ...(process.env.CONSILITH_SIWC_MODEL?.trim() ? { defaultModel: process.env.CONSILITH_SIWC_MODEL.trim() } : {}),
});

if (proxyPort) {
  const proxy = createCallbackProxy(callbackPort);
  proxy.on("error", () => fail("Could not bind the configured callback proxy port."));
  proxy.listen({ port: proxyPort, host });
}
server.listen({ port, host }, () => {
  console.log(`consilith-siwc listening on http://${host}:${port}`);
});
server.on("error", (error) => fail(`could not listen on ${host}:${port} (${(error as Error).message})`));
