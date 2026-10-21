import { homedir } from "node:os";
import { readFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { createApp, LoginBridge } from "./app.js";
import { createOsCredentialEncryption } from "./credential-encryption.js";
import { loadDevkit } from "./devkit.js";
import { createCallbackProxy } from "./callback-proxy.js";

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

const bridge = new LoginBridge();
const credentialEncryption = createOsCredentialEncryption(home);
if (!await credentialEncryption.isAvailable()) {
  fail("OS credential encryption is unavailable. Unlock the operating-system credential store and restart.");
}
const client = devkit.createChatGPT({
  appName: "Consilith",
  appId: process.env.CONSILITH_SIWC_APP_ID?.trim() || "consilith-siwc",
  redirectPort: callbackPort,
  storageDir: home,
  credentialEncryption,
  openBrowser: bridge.openBrowser,
});

const server = createApp({
  secret,
  client,
  bridge,
  ...(process.env.CONSILITH_SIWC_MANAGED === "1" ? { shutdown: () => {
    client.cancelSignIn();
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
