import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "@/shared/http/client";
import { ThemeSwitch } from "@/shared/theme/ThemeSwitch";
import { Mark } from "@/shared/ui";
import { useAuth } from "../AuthProvider";
import { authApi, type SignedIn } from "../api";
import { safeNext } from "../guards";
import s from "./auth.module.css";
import { Welcome } from "./Welcome";

function landing(r: SignedIn): string {
  if (r.needsProfile) return "/welcome/about";
  let next = "/";
  try {
    next = safeNext(sessionStorage.getItem("consilith.next"));
    sessionStorage.removeItem("consilith.next");
  } catch {
    /* default to Home */
  }
  return next;
}

/** Redeems a one-time token from the URL, then replaces the URL so the token is gone. */
function useRedeem(param: string, redeem: (v: string) => Promise<SignedIn>) {
  const { signIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ name: string; to: string } | null>(null);
  const once = useRef(false);

  useEffect(() => {
    if (once.current) return;
    once.current = true;
    const value = new URLSearchParams(location.search).get(param);
    history.replaceState(null, "", location.pathname);
    if (!value) {
      setError("This link is incomplete.");
      return;
    }
    redeem(value).then(
      (r) => {
        signIn(r);
        setDone({ name: r.person.name, to: landing(r) });
      },
      (e) => setError(e instanceof ApiError ? e.message : "This link could not be used."),
    );
  }, [param, redeem, signIn]);
  return { error, done };
}

function Shell({ error }: { error: string | null }) {
  return (
    <div className={s.page}>
      <div className={s.corner}>
        <ThemeSwitch />
      </div>
      <main className={s.center}>
        <div
          className={s.card}
          style={{ minHeight: 360, alignItems: "center", textAlign: "center" }}
        >
          <Mark size={56} loading={!error} label={error ? "Consilith" : "Signing you in"} />
          {error ? (
            <>
              <h1 className={s.title} style={{ fontSize: 32 }}>
                That link did not work.
              </h1>
              <p className={s.sub}>
                {error} Ask for a new one; each link works once, for 15 minutes.
              </p>
              <Link to="/signin">Back to sign in</Link>
            </>
          ) : (
            <p className={s.sub} role="status">
              Signing you in…
            </p>
          )}
        </div>
      </main>
    </div>
  );
}

export function VerifyPage() {
  const { error, done } = useRedeem("token", authApi.redeemLink);
  return done ? <Welcome name={done.name} to={done.to} /> : <Shell error={error} />;
}
