import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "@/shared/http/client";
import { ThemeSwitch } from "@/shared/theme/ThemeSwitch";
import { Mark } from "@/shared/ui";
import { useAuth } from "../AuthProvider";
import { authApi } from "../api";
import s from "./auth.module.css";
import { Welcome } from "./Welcome";

/** The GitHub callback lands here with a one-time ticket; the URL is cleaned before redeeming. */
export function FinishPage() {
  const { signIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ name: string; to: string } | null>(null);
  const once = useRef(false);

  useEffect(() => {
    if (once.current) return;
    once.current = true;
    const ticket = new URLSearchParams(location.search).get("ticket");
    const back = new URLSearchParams(location.search).get("return") ?? "/";
    history.replaceState(null, "", location.pathname);
    if (!ticket) {
      setError("GitHub did not finish the sign-in.");
      return;
    }
    authApi.redeemTicket(ticket).then(
      (r) => {
        signIn(r);
        const safe = back.startsWith("/") && !back.startsWith("//") ? back : "/";
        setDone({ name: r.person.name, to: r.needsProfile ? "/welcome/about" : safe });
      },
      (e) => setError(e instanceof ApiError ? e.message : "GitHub sign-in failed."),
    );
  }, [signIn]);

  if (done) return <Welcome name={done.name} to={done.to} />;
  return (
    <div className={s.page}>
      <div className={s.corner}>
        <ThemeSwitch />
      </div>
      <main className={s.center}>
        <div
          className={s.card}
          style={{ minHeight: 320, alignItems: "center", textAlign: "center" }}
        >
          <Mark size={56} loading={!error} label="Signing you in" />
          {error ? (
            <>
              <p className={s.sub}>{error}</p>
              <Link to="/signin">Back to sign in</Link>
            </>
          ) : (
            <p className={s.sub} role="status">
              Signing you in with GitHub…
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
