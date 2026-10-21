import { isAbsolute } from "node:path";
import { pathToFileURL } from "node:url";
import type { DevkitModule } from "./types.js";

/**
 * The Sign in with ChatGPT DevKit (`@siwc/local`) is not published to npm and carries a
 * noncommercial licence, so it is not vendored here. Install it yourself (see README), either
 * as the `@siwc/local` package or by pointing CONSILITH_SIWC_DEVKIT at its built `dist/index.js`.
 */
export async function loadDevkit(env: NodeJS.ProcessEnv = process.env): Promise<DevkitModule> {
  const explicit = env.CONSILITH_SIWC_DEVKIT?.trim();
  let mod: unknown;
  try {
    if (explicit) {
      if (!isAbsolute(explicit)) throw new Error("CONSILITH_SIWC_DEVKIT must be an absolute path to dist/index.js");
      mod = await import(pathToFileURL(explicit).href);
    } else {
      const name = "@siwc/local"; // variable specifier: not resolved at compile time
      mod = await import(name);
    }
  } catch (cause) {
    const detail = cause instanceof Error ? cause.message : String(cause);
    throw new Error(
      "The Sign in with ChatGPT DevKit (@siwc/local) is not installed or could not be loaded " +
        `(${detail}). See apps/siwc/README.md, section "Install the DevKit".`,
    );
  }
  if (!mod || typeof (mod as DevkitModule).createChatGPT !== "function") {
    throw new Error("The loaded DevKit module does not export createChatGPT.");
  }
  return mod as DevkitModule;
}
