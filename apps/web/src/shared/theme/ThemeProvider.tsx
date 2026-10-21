import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
export type Mode = "light" | "dark";

const KEY = "consilith.theme";

type ThemeValue = { choice: ThemeChoice; mode: Mode; setChoice: (c: ThemeChoice) => void };
const ThemeContext = createContext<ThemeValue | null>(null);

function readChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemMode(): Mode {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

/** Light and dark are complete token sets; the person's pick wins over the system's. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(readChoice);
  const [system, setSystem] = useState<Mode>(systemMode);

  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const q = matchMedia("(prefers-color-scheme: dark)");
    const on = () => setSystem(q.matches ? "dark" : "light");
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);

  const mode: Mode = choice === "system" ? system : choice;

  useEffect(() => {
    const root = document.documentElement;
    if (choice === "system") delete root.dataset.theme;
    else root.dataset.theme = choice;
  }, [choice]);

  const setChoice = useCallback((c: ThemeChoice) => {
    setChoiceState(c);
    try {
      if (c === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, c);
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  }, []);

  return <ThemeContext value={{ choice, mode, setChoice }}>{children}</ThemeContext>;
}

export function useTheme(): ThemeValue {
  const v = useContext(ThemeContext);
  if (!v) throw new Error("useTheme outside ThemeProvider");
  return v;
}
