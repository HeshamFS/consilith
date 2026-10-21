import { execFileSync } from "node:child_process";
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { CredentialEncryption } from "./types.js";

// The DevKit refuses to persist tokens without an OS-backed encryption provider and
// forbids plaintext or hard-coded keys. This provider encrypts with AES-256-GCM using a
// random 256-bit key that is itself protected by the operating system:
//   Windows: DPAPI (CurrentUser scope), key blob kept in <dir>/credential-key.dpapi
//   macOS:   login keychain (`security`)
//   Linux:   libsecret (`secret-tool`)
// Only the Windows path has been exercised on this machine.

const SERVICE = "consilith-siwc";
const KEY_BYTES = 32;

function run(file: string, args: string[], input?: string): string {
  return execFileSync(file, args, {
    input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    timeout: 20_000,
  }).trim();
}

function dpapi(mode: "Protect" | "Unprotect", b64: string): string {
  const script =
    "Add-Type -AssemblyName System.Security;" +
    "$in=[Convert]::FromBase64String([Console]::In.ReadToEnd().Trim());" +
    `$out=[Security.Cryptography.ProtectedData]::${mode}($in,$null,'CurrentUser');` +
    "[Console]::Out.Write([Convert]::ToBase64String($out))";
  return run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], b64);
}

function loadOrCreateKey(dir: string): Buffer {
  if (process.platform === "win32") {
    mkdirSync(dir, { recursive: true });
    const file = join(dir, "credential-key.dpapi");
    if (existsSync(file)) return Buffer.from(dpapi("Unprotect", readFileSync(file, "utf8")), "base64");
    const key = randomBytes(KEY_BYTES);
    writeFileSync(file, dpapi("Protect", key.toString("base64")), { mode: 0o600 });
    return key;
  }
  if (process.platform === "darwin") {
    try {
      return Buffer.from(run("security", ["find-generic-password", "-a", SERVICE, "-s", SERVICE, "-w"]), "base64");
    } catch {
      const key = randomBytes(KEY_BYTES);
      run("security", ["add-generic-password", "-a", SERVICE, "-s", SERVICE, "-w", key.toString("base64"), "-U"]);
      return key;
    }
  }
  // Linux and others: libsecret.
  try {
    const stored = run("secret-tool", ["lookup", "service", SERVICE]);
    if (stored) return Buffer.from(stored, "base64");
  } catch {
    /* not stored yet */
  }
  const key = randomBytes(KEY_BYTES);
  run("secret-tool", ["store", "--label=Consilith SIWC key", "service", SERVICE], key.toString("base64"));
  return key;
}

export function createOsCredentialEncryption(dir: string): CredentialEncryption {
  let key: Buffer | undefined;
  const getKey = (): Buffer => {
    key ??= loadOrCreateKey(dir);
    if (key.length !== KEY_BYTES) {
      key = undefined;
      throw new Error("credential key has the wrong size");
    }
    return key;
  };
  if (process.platform !== "win32") {
    try { mkdirSync(dir, { recursive: true, mode: 0o700 }); chmodSync(dir, 0o700); } catch { /* best effort */ }
  }
  return {
    id: "consilith-os-aesgcm-v1",
    isAvailable() {
      try { getKey(); return true; } catch { return false; }
    },
    encrypt(plaintext) {
      const iv = randomBytes(12);
      const cipher = createCipheriv("aes-256-gcm", getKey(), iv);
      const body = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
      return Buffer.concat([iv, cipher.getAuthTag(), body]);
    },
    decrypt(ciphertext) {
      const buf = Buffer.from(ciphertext);
      if (buf.length < 29) throw new Error("ciphertext too short");
      const decipher = createDecipheriv("aes-256-gcm", getKey(), buf.subarray(0, 12));
      decipher.setAuthTag(buf.subarray(12, 28));
      return Buffer.concat([decipher.update(buf.subarray(28)), decipher.final()]).toString("utf8");
    },
  };
}
