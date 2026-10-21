import { type FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { ApiError } from "@/shared/http/client";
import { ThemeSwitch } from "@/shared/theme/ThemeSwitch";
import { Button, Icon, Mark, TextField } from "@/shared/ui";
import { ART } from "@/shared/ui/art.generated";
import { authApi, githubStartUrl, type MailItem, type Providers } from "../api";
import { safeNext } from "../guards";
import s from "./auth.module.css";

function Art({ name, className }: { name: keyof typeof ART; className: string }) {
  return (
    <div
      className={className}
      aria-hidden
      // biome-ignore lint/security/noDangerouslySetInnerHtml: static art generated from the repo's design source
      dangerouslySetInnerHTML={{ __html: ART[name] }}
    />
  );
}

const GITHUB =
  "M12 2C6.48 2 2 6.58 2 12.25c0 4.53 2.87 8.37 6.84 9.73.5.1.68-.22.68-.49v-1.7c-2.78.62-3.37-1.38-3.37-1.38-.45-1.18-1.11-1.5-1.11-1.5-.91-.64.07-.63.07-.63 1 .07 1.53 1.06 1.53 1.06.9 1.57 2.35 1.12 2.92.85.09-.66.35-1.12.64-1.38-2.22-.26-4.56-1.14-4.56-5.07 0-1.12.39-2.04 1.03-2.76-.1-.26-.45-1.3.1-2.71 0 0 .84-.28 2.75 1.05a9.32 9.32 0 0 1 5 0c1.91-1.33 2.75-1.05 2.75-1.05.55 1.41.2 2.45.1 2.71.64.72 1.03 1.64 1.03 2.76 0 3.94-2.34 4.81-4.57 5.06.36.32.68.94.68 1.9v2.81c0 .27.18.6.69.49A10.1 10.1 0 0 0 22 12.25C22 6.58 17.52 2 12 2Z";

export function SignInPage() {
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<Providers | null>(null);
  const [mail, setMail] = useState<MailItem[]>([]);

  useEffect(() => {
    authApi
      .providers()
      .then(setProviders, () => setProviders({ email: true, github: false, devMailbox: false }));
  }, []);

  useEffect(() => {
    if (!sent || !providers?.devMailbox) return;
    const load = () => authApi.mailbox().then(setMail, () => setMail([]));
    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [sent, providers?.devMailbox]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("Enter your work email, like name@lab.org.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      sessionStorage.setItem("consilith.next", next);
    } catch {
      /* the link still signs in; it lands on Home */
    }
    try {
      await authApi.emailLink(email.trim());
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const mine = mail.filter((m) => m.to === email.trim().toLowerCase()).slice(0, 1);

  return (
    <div className={s.page}>
      <div className={s.rules} aria-hidden />
      <div className={s.corner}>
        <ThemeSwitch />
      </div>
      <Art name="sketch" className={`${s.art} ${s.artSketch}`} />
      <Art name="grains" className={`${s.art} ${s.artGrains}`} />
      <Art name="lattice" className={`${s.art} ${s.artLattice}`} />
      <Art name="cursors" className={`${s.art} ${s.artCursors}`} />

      <main className={s.center}>
        <div className={s.card}>
          <div className={s.brand}>
            <Mark size={56} loading={busy} label="Consilith" />
            <span className={s.word}>consilith</span>
          </div>
          <div className={s.lead}>
            <h1 className={s.title}>
              Built together.
              <br />
              <i>Checked apart.</i>
            </h1>
            <p className={s.sub}>
              Sketch it, drop it or type it. Your colleagues and their agents work on one model;
              every number is checked by a method that did not produce it.
            </p>
          </div>

          {sent ? (
            <div className={s.sent} role="status">
              <Icon name="verified" size={28} />
              <div>
                <strong>Check your email.</strong>
                <p>
                  A sign-in link is on its way to <span className="mono">{email.trim()}</span>. It
                  works once and for 15 minutes.
                </p>
              </div>
              {providers?.devMailbox ? (
                <div className={s.dev}>
                  <span className={s.devTag}>dev mailbox</span>
                  {mine[0] ? (
                    <a href={mine[0].url} className={s.devLink}>
                      Open the sign-in link
                    </a>
                  ) : (
                    <span className={s.devWait}>waiting for the link…</span>
                  )}
                </div>
              ) : null}
              <Button variant="ghost" small onClick={() => setSent(false)}>
                Use another email
              </Button>
            </div>
          ) : (
            <form className={s.form} onSubmit={submit} noValidate>
              <TextField
                label="Work email"
                type="email"
                autoComplete="email"
                inputMode="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                error={error}
                required
              />
              <Button type="submit" block disabled={busy}>
                {busy ? "Sending…" : "Email me a sign-in link"}
              </Button>
            </form>
          )}

          {!sent ? (
            <>
              <div className={s.or}>
                <span />
                or
                <span />
              </div>
              {providers?.github ? (
                <a className={s.github} href={githubStartUrl(next)}>
                  <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
                    <path d={GITHUB} fill="currentColor" />
                  </svg>
                  Continue with GitHub
                </a>
              ) : (
                <span
                  className={s.githubOff}
                  title="GitHub sign-in is not configured on this server"
                >
                  <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
                    <path d={GITHUB} fill="currentColor" />
                  </svg>
                  Continue with GitHub
                  <span className={s.offTag}>not set up here</span>
                </span>
              )}
            </>
          ) : null}
          <div className={s.foot}>
            No password. Invited? Your link brings you straight to the room.
          </div>
        </div>
      </main>
      <div className={s.tagline}>Multiplayer science studio</div>
    </div>
  );
}
