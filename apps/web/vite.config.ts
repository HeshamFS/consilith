import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type UserConfig } from "vite";

// The room server owns auth and streams; the dev server proxies /api to it on the same origin,
// so the Bearer header and the OAuth cookie behave as they will behind the production gateway.
const ROOM = process.env.CONSILITH_ROOM_URL ?? "http://127.0.0.1:8700";
// The dev mailbox needs a capability header; the proxy adds it so the browser never holds it.
// The room writes a fresh one to var/dev.env at every start, so it is read per request.
const DEV_ENV = fileURLToPath(new URL("../../var/dev.env", import.meta.url));
function devCapability(): string {
  try {
    const line = readFileSync(DEV_ENV, "utf8")
      .split(/\r?\n/)
      .find((l) => l.startsWith("CONSILITH_DEV_CAPABILITY="));
    return line ? line.slice(line.indexOf("=") + 1).trim() : "";
  } catch {
    return process.env.CONSILITH_DEV_CAPABILITY ?? "";
  }
}

const strip = (p: string) => p.replace(/^\/api/, "");

const config: UserConfig = {
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    host: "127.0.0.1",
    port: Number(process.env.CONSILITH_WEB_PORT ?? 5173),
    strictPort: true,
    proxy: {
      "/api/dev": {
        target: ROOM,
        rewrite: strip,
        // No forwarding headers (no xfwd): the room refuses the mailbox behind a forwarder.
        configure: (proxy) => {
          proxy.on("proxyReq", (req) => {
            const cap = devCapability();
            if (cap) req.setHeader("X-Consilith-Dev", cap);
          });
        },
      },
      "/api": { target: ROOM, rewrite: strip },
    },
  },
  build: { target: "es2023", manifest: true, sourcemap: true },
};

export default defineConfig(({ command }) => {
  if (command === "build" && process.env.VITE_API_MOCK === "1") {
    throw new Error("VITE_API_MOCK is for vite dev only; a build never ships the mock API.");
  }
  return config;
});
