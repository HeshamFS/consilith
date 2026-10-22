import { useEffect, useState } from "react";
import { CORE, PETAL } from "@/shared/ui/Mark";
import s from "./chat-trace.module.css";

const QUERY = "(prefers-reduced-motion: reduce)";

/** True while the person asks the system for less motion. The stylesheet honours the same query;
 * this mirror lets the component say so in its markup (`data-motion`), which is what tests read. */
export function useReducedMotion(): boolean {
  const read = () =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(QUERY).matches
      : false;
  const [reduced, setReduced] = useState(read);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(QUERY);
    const on = () => setReduced(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

/** The Consilith mark at reply size. While `thinking`, its four petals pulse in a wave (opacity
 * 1 to 0.35 to 1 over 2 s, 160 ms apart); it never rotates. Reduced motion: a still mark. */
export function ThinkingMark({
  thinking = false,
  size = 22,
}: {
  thinking?: boolean;
  size?: number;
}) {
  const reduced = useReducedMotion();
  const pulse = thinking && !reduced;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      className={s.mark}
      data-testid="agent-mark"
      data-thinking={pulse ? "true" : "false"}
      aria-hidden
    >
      {[0, 1, 2, 3].map((i) => (
        <g key={i} transform={`rotate(${i * 90} 50 50)`}>
          <path
            className={pulse ? s.petalPulse : undefined}
            style={pulse ? { animationDelay: `${i * 160}ms` } : undefined}
            d={PETAL}
            fill="currentColor"
          />
        </g>
      ))}
      <path d={CORE} fill="currentColor" />
    </svg>
  );
}

/** What the agent is doing while no text has arrived: the pulsing mark and a shimmering label.
 * It sits in the reply's own gutter, so the text takes its place without a jump. */
export function ThinkingLine({ label }: { label: string }) {
  const reduced = useReducedMotion();
  return (
    <div className={s.reply} data-testid="thinking-line" data-motion={reduced ? "reduced" : "full"}>
      <span className={s.gutter}>
        <ThinkingMark thinking />
      </span>
      <span className={reduced ? s.thinkLabelStill : s.thinkLabel}>{label}</span>
    </div>
  );
}
