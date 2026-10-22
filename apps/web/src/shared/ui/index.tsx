import type { ButtonHTMLAttributes, CSSProperties, InputHTMLAttributes, ReactNode } from "react";
import { useId } from "react";
import { Link, type LinkProps } from "react-router";
import { Icon, type IconName } from "./Icon";
import s from "./ui.module.css";

export { Icon, type IconName } from "./Icon";
export { Lockup, Mark } from "./Mark";
export { ui };

const ui = s;

type Variant = "primary" | "secondary" | "ghost" | "accent";
type BtnOwn = { variant?: Variant; small?: boolean; block?: boolean; accent?: string };

function cls(variant: Variant, small?: boolean, block?: boolean): string {
  return [s.btn, s[variant], small ? s.small : "", block ? s.block : ""].join(" ");
}

export function Button({
  variant = "primary",
  small,
  block,
  accent,
  style,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & BtnOwn) {
  const st = accent ? ({ ...style, "--accent": accent } as CSSProperties) : style;
  return <button type={type} className={cls(variant, small, block)} style={st} {...rest} />;
}

export function ButtonLink({
  variant = "primary",
  small,
  block,
  ...rest
}: LinkProps & Omit<BtnOwn, "accent">) {
  return <Link className={cls(variant, small, block)} {...rest} />;
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  leading?: ReactNode;
};

export function TextField({ label, hint, error, leading, id, ...rest }: FieldProps) {
  const auto = useId();
  const fid = id ?? auto;
  const describedBy = error ? `${fid}-error` : hint ? `${fid}-hint` : undefined;
  return (
    <div className={s.field}>
      <label className={s.label} htmlFor={fid}>
        {label}
      </label>
      <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
        {leading}
        <input
          id={fid}
          className={s.input}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          {...rest}
        />
      </div>
      {error ? (
        <span id={`${fid}-error`} className={s.error}>
          {error}
        </span>
      ) : hint ? (
        <span id={`${fid}-hint`} className={s.hint}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}

export const SEAT_COLORS = ["blue", "magenta", "teal", "violet", "amber", "olive"] as const;
export type SeatColor = (typeof SEAT_COLORS)[number];

// The room document stores a seat's colour as its paper and stage hex values
// (apps/room/src/consilith_room/domain/roles.py COLORS); the UI uses the name, so the theme picks
// the right variant for light and dark.
const SEAT_HEX: Record<string, SeatColor> = {
  "#2a50cc": "blue",
  "#7c9bff": "blue",
  "#c4296a": "magenta",
  "#ff7fb5": "magenta",
  "#0d8089": "teal",
  "#3ec1b4": "teal",
  "#5b2ea6": "violet",
  "#b497f0": "violet",
  "#b26a00": "amber",
  "#f2b04a": "amber",
  "#5e6b1f": "olive",
  "#b7c45e": "olive",
};

export function seatName(color: string | null | undefined): SeatColor {
  const c = (color ?? "").toLowerCase();
  if ((SEAT_COLORS as readonly string[]).includes(c)) return c as SeatColor;
  return SEAT_HEX[c] ?? "blue";
}

export function seatVar(color: string | null | undefined): string {
  return `var(--seat-${seatName(color)})`;
}

export function Avatar({ name, color, size = 32 }: { name: string; color: string; size?: number }) {
  return (
    <span
      className={s.avatar}
      style={{ width: size, height: size, background: seatVar(color), fontSize: size * 0.4 }}
      aria-hidden
    >
      {(name.trim()[0] ?? "?").toUpperCase()}
    </span>
  );
}

export type StampKind = "verified" | "warning" | "blocked" | "checking";
const STAMP_ICON: Record<StampKind, IconName> = {
  verified: "verified",
  warning: "warning",
  blocked: "blocked",
  checking: "checking",
};

export function Stamp({
  kind,
  children,
  iconOnly = false,
  title,
}: {
  kind: StampKind;
  children?: ReactNode;
  /** Just the icon, the words in its tooltip and accessible name (lists, the ledger). */
  iconOnly?: boolean;
  title?: string;
}) {
  if (iconOnly)
    return (
      <span
        className={`${s.stampIcon} ${s[kind]}`}
        role="img"
        aria-label={title ?? kind}
        title={title ?? kind}
      >
        <Icon name={STAMP_ICON[kind]} size={14} />
      </span>
    );
  return (
    <span className={`${s.stamp} ${s[kind]}`}>
      <Icon name={STAMP_ICON[kind]} size={13} />
      {children ?? kind}
    </span>
  );
}

export function Illustrative() {
  return (
    <span className={s.illustrative} title="Illustrative design data, not a solver result">
      illustrative
    </span>
  );
}

export function Caps({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <span className={s.caps} style={style}>
      {children}
    </span>
  );
}

/** A small "?" that explains on hover or focus, so screens carry labels, not paragraphs. */
export function Info({ text }: { text: string }) {
  return (
    <button type="button" className={s.info} aria-label={text} data-tip={text}>
      ?
    </button>
  );
}
