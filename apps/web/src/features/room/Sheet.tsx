import { type ReactNode, useEffect, useId, useRef } from "react";
import s from "./room.module.css";

/** A right-hand sheet over the room (full screen on phones). Escape and the scrim close it;
 * focus moves in on open and returns to the opener on close. */
export function Sheet({
  title,
  sub,
  icon,
  narrow = false,
  onClose,
  children,
}: {
  title: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  narrow?: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [onClose]);
  return (
    <>
      <button
        type="button"
        className={s.scrim}
        aria-label="Close"
        tabIndex={-1}
        onClick={onClose}
      />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        tabIndex={-1}
        className={`${s.sheet} ${narrow ? s.sheetNarrow : ""} ${s.floating}`}
      >
        <header className={s.sheetHead}>
          {icon}
          <div style={{ minWidth: 0 }}>
            <h2 id={id} className={s.sheetTitle}>
              {title}
            </h2>
            {sub ? <p className={s.sheetSub}>{sub}</p> : null}
          </div>
          <button type="button" className={s.close} aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </header>
        {/* biome-ignore lint/a11y/noNoninteractiveTabindex: the scroll region needs a keyboard target even when its content is read-only */}
        <section className={s.sheetBody} aria-labelledby={id} tabIndex={0}>
          {children}
        </section>
      </div>
    </>
  );
}
