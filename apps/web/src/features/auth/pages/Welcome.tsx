import { useEffect } from "react";
import { useNavigate } from "react-router";
import { ThemeSwitch } from "@/shared/theme/ThemeSwitch";
import { Mark } from "@/shared/ui";
import s from "./auth.module.css";

/** The moment after a sign-in link or GitHub works: the mark assembles, then you land. */
export function Welcome({ name, to }: { name: string; to: string }) {
  const navigate = useNavigate();
  useEffect(() => {
    // Same length with reduced motion: the petals then fade in place instead of flying in.
    const t = setTimeout(() => navigate(to, { replace: true }), 3400);
    return () => clearTimeout(t);
  }, [navigate, to]);
  return (
    <div className={s.page}>
      <div className={s.corner}>
        <ThemeSwitch />
      </div>
      <main className={s.center}>
        <div className={s.welcome} role="status">
          <Mark
            size={120}
            intro
            seats={[
              "var(--seat-blue)",
              "var(--seat-magenta)",
              "var(--seat-teal)",
              "var(--seat-amber)",
            ]}
            label="Consilith"
          />
          <p className={s.welcomeText}>{name ? `Welcome, ${name}` : "Welcome to Consilith"}</p>
        </div>
      </main>
    </div>
  );
}
