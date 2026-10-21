import { createServer, request, type Server } from "node:http";

/** Docker ingress only: retain the SDK's loopback origin, path, state and PKCE checks. */
export function createCallbackProxy(callbackPort: number): Server {
  if (!Number.isInteger(callbackPort) || callbackPort < 1 || callbackPort > 65535) {
    throw new Error("A fixed SDK callback port is required");
  }
  const expectedHost = `127.0.0.1:${callbackPort}`;
  const server = createServer({ maxHeaderSize: 16 * 1024 }, (incoming, outgoing) => {
    outgoing.setHeader("Cache-Control", "no-store");
    outgoing.setHeader("Referrer-Policy", "no-referrer");
    const path = incoming.url ?? "";
    if (incoming.method !== "GET" || incoming.headers.host !== expectedHost ||
        !path.startsWith("/auth/callback?") || path.length > 8192) {
      incoming.resume();
      outgoing.writeHead(404).end("Not found");
      return;
    }
    // Forward no cookies, authorization headers or arbitrary destination. Never log the URL.
    const upstream = request({
      hostname: "127.0.0.1", port: callbackPort, method: "GET", path,
      headers: { host: expectedHost }, timeout: 15_000,
    }, (reply) => {
      outgoing.writeHead(reply.statusCode ?? 502, reply.headers);
      reply.pipe(outgoing);
    });
    upstream.on("timeout", () => upstream.destroy(new Error("callback timeout")));
    upstream.on("error", () => {
      if (!outgoing.headersSent) outgoing.writeHead(503);
      outgoing.end("No active sign-in callback");
    });
    outgoing.on("close", () => upstream.destroy());
    incoming.resume();
    upstream.end();
  });
  server.requestTimeout = 20_000;
  server.headersTimeout = 10_000;
  return server;
}
