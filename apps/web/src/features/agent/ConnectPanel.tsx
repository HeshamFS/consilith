import { useEffect, useState } from "react";
import { ApiError } from "@/shared/http/client";
import { Button } from "@/shared/ui";
import type { CredentialState, ProviderCredential, ProviderInfo } from "./api";
import s from "./connect.module.css";

export const STATE_LABEL: Record<CredentialState, string> = {
  not_connected: "Not connected",
  validating: "Checking…",
  ready: "Ready",
  invalid: "Not accepted",
  expired: "Sign-in ended",
  needs_reentry: "Enter your key again",
};

export type ConnectPanelProps = {
  /** The provider's registry entry: its auth kind, label and data-terms line. */
  provider: ProviderInfo;
  /** What the server says about this connection. */
  credential: ProviderCredential | undefined;
  /** Key providers: send the key. The server checks it with the provider before keeping it, so a
   * resolved promise means the key works and a rejected one means it was refused. */
  onSaveKey?: (key: string) => Promise<void>;
  /** ChatGPT: start the sign-in (resolves when it finishes), and sign out. */
  onConnectChatGPT?: () => Promise<void>;
  onDisconnectChatGPT?: () => Promise<void>;
  /** The signed-in ChatGPT account, when the server reports one. */
  email?: string;
  /** Several ChatGPT accounts are turned on: say how to pick the right one, and show the email
   * masked so the person can confirm it is theirs. */
  multi?: boolean;
  /** ChatGPT: finish the sign-in from the address the browser ended on (a browser that is not on
   * the host). Offered only while a sign-in is waiting. */
  onPasteCallback?: (address: string) => Promise<void>;
  /** The person left their last room: offer to disconnect their ChatGPT account too. */
  offerDisconnect?: boolean;
};

function problemText(e: unknown): string {
  return e instanceof ApiError ? e.message : "That did not work. Try again.";
}

/** One provider's connection, driven by the registry: its state, the data-terms line, how long the
 * connection lasts, and the control that connects it. It never switches the chosen provider. */
export function ConnectPanel({
  provider,
  credential,
  onSaveKey,
  onConnectChatGPT,
  onDisconnectChatGPT,
  email,
  multi,
  onPasteCallback,
  offerDisconnect,
}: ConnectPanelProps) {
  const [key, setKey] = useState("");
  const [address, setAddress] = useState("");
  const [pending, setPending] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // A new answer from the server replaces whatever this panel last showed on its own.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when the server's state changes
  useEffect(() => {
    setRefused(null);
    setSaved(false);
  }, [credential?.state, provider.id]);

  const state: CredentialState = pending
    ? "validating"
    : refused
      ? "invalid"
      : saved
        ? "ready"
        : (credential?.state ?? "not_connected");
  const reason = refused ?? (state === "ready" ? null : (credential?.reason ?? null));

  // Not a <form>: this panel sits inside the settings page's form, and a nested form makes the
  // browser submit natively and reload the page before the key is sent (found live).
  async function submitKey(e?: { preventDefault: () => void }) {
    e?.preventDefault();
    const value = key.trim();
    if (!value || !onSaveKey || pending) return;
    setPending(true);
    setRefused(null);
    try {
      await onSaveKey(value);
      setSaved(true);
    } catch (err) {
      setRefused(problemText(err));
    } finally {
      setKey("");
      setPending(false);
    }
  }

  function finishSignIn() {
    const value = address.trim();
    if (!value || !onPasteCallback) return;
    setAddress("");
    void onPasteCallback(value).catch((err: unknown) => setRefused(problemText(err)));
  }

  async function run(action: (() => Promise<void>) | undefined) {
    if (!action || pending) return;
    setPending(true);
    setRefused(null);
    try {
      await action();
    } catch (err) {
      setRefused(problemText(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <section className={s.panel} aria-label={`${provider.label} connection`} data-state={state}>
      <div className={s.head}>
        <strong>{provider.label}</strong>
        <span className={s.state} role="status" data-state={state}>
          {STATE_LABEL[state]}
        </span>
      </div>
      {reason ? (
        <p className={s.reason} role="alert">
          {reason}
        </p>
      ) : null}

      {provider.auth === "key" ? (
        <fieldset className={`${s.row} ${s.group}`} aria-label="Connect with your key">
          <label className={s.field}>
            <span className={s.label}>API key</span>
            <input
              className={s.input}
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={
                state === "ready" ? "held for this session" : "paste your key, then connect"
              }
              value={key}
              disabled={pending}
              onChange={(e) => setKey(e.target.value)}
              onKeyDown={(e) => {
                // Enter connects the key; it never submits the page's form.
                if (e.key === "Enter") void submitKey(e);
              }}
            />
          </label>
          <Button
            type="button"
            variant="secondary"
            disabled={!key.trim() || pending}
            onClick={() => void submitKey()}
          >
            {pending ? "Checking…" : state === "ready" ? "Replace key" : "Connect"}
          </Button>
        </fieldset>
      ) : null}

      {provider.auth === "chatgpt" ? (
        <div className={s.row}>
          {state === "ready" ? (
            <>
              <span className={s.account}>
                {email
                  ? multi
                    ? `Signed in as ${email}. Not your account? Disconnect and try again.`
                    : `Signed in as ${email}`
                  : "Signed in"}
              </span>
              <Button
                variant="ghost"
                disabled={pending}
                onClick={() => void run(onDisconnectChatGPT)}
              >
                {offerDisconnect ? "Disconnect ChatGPT" : "Disconnect"}
              </Button>
            </>
          ) : (
            <Button
              variant="secondary"
              disabled={pending || state === "validating"}
              onClick={() => void run(onConnectChatGPT)}
            >
              {pending || state === "validating"
                ? "Finish signing in in the new tab…"
                : state === "expired"
                  ? "Sign in with ChatGPT again"
                  : "Continue with ChatGPT"}
            </Button>
          )}
        </div>
      ) : null}

      {provider.auth === "chatgpt" && multi && state !== "ready" ? (
        <p className={s.terms}>
          Use a private window, or sign out of ChatGPT first. Otherwise ChatGPT can sign in with
          another person's account.
        </p>
      ) : null}

      {provider.auth === "chatgpt" && offerDisconnect ? (
        <p className={s.terms}>
          You left your last room. Disconnect ChatGPT too if you no longer need it here.
        </p>
      ) : null}

      {provider.auth === "chatgpt" && onPasteCallback && state === "validating" ? (
        <fieldset className={`${s.row} ${s.group}`} aria-label="Finish ChatGPT sign-in">
          <label className={s.field}>
            <span className={s.label}>Address ChatGPT sent you to</span>
            <input
              className={s.input}
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="paste it here if the page could not open"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  finishSignIn();
                }
              }}
            />
          </label>
          <Button
            type="button"
            variant="secondary"
            disabled={!address.trim()}
            onClick={finishSignIn}
          >
            Finish sign-in
          </Button>
        </fieldset>
      ) : null}

      {provider.auth === "none" ? <p className={s.terms}>Nothing to connect.</p> : null}

      <p className={s.terms}>{provider.dataTerms}</p>
      {credential?.lifetime ? <p className={s.lifetime}>{credential.lifetime}</p> : null}
    </section>
  );
}
