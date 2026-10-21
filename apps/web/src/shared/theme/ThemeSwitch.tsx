import { type ThemeChoice, useTheme } from "./ThemeProvider";
import s from "./theme.module.css";

const OPTIONS: { v: ThemeChoice; label: string; d: string }[] = [
  {
    v: "light",
    label: "Light",
    d: "M12 5V3M12 21v-2M5 12H3M21 12h-2M7 7 5.6 5.6M18.4 18.4 17 17M7 17l-1.4 1.4M18.4 5.6 17 7M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
  },
  { v: "system", label: "Match the system", d: "M4 5h16v11H4zM9 20h6M12 16v4" },
  { v: "dark", label: "Dark", d: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" },
];

/** Light, system or dark. Compact enough for the top bar on a phone. */
export function ThemeSwitch() {
  const { choice, setChoice } = useTheme();
  return (
    <fieldset className={s.switch}>
      <legend className="visually-hidden">Colour mode</legend>
      {OPTIONS.map((o) => (
        <button
          key={o.v}
          type="button"
          aria-pressed={choice === o.v}
          aria-label={o.label}
          title={o.label}
          className={s.opt}
          onClick={() => setChoice(o.v)}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d={o.d} />
          </svg>
        </button>
      ))}
    </fieldset>
  );
}
