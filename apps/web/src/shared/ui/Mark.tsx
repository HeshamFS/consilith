import styles from "./Mark.module.css";

const PETAL = "M50 40 C59 31 59 15 50 6 C41 15 41 31 50 40 Z";
const CORE = "M50 43 L57 50 L50 57 L43 50 Z";

type Props = {
  size?: number;
  /** Room mark: one colour per filled seat (up to four); empty seats are dashed. */
  seats?: string[];
  /** Loader: petals arrive one by one, then the core is stamped. */
  loading?: boolean;
  /** Plays once: the four petals converge from their seats, then the core is stamped. */
  intro?: boolean;
  label?: string;
};

/** Logo A, the converging petals. One petal per seat. */
export function Mark({ size = 26, seats, loading = false, intro = false, label }: Props) {
  const room = seats !== undefined;
  const svg = (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      className={intro ? styles.intro : loading ? styles.loading : undefined}
      aria-hidden
    >
      {[0, 1, 2, 3].map((i) => {
        const fill = room ? seats[i] : "currentColor";
        return (
          <g key={i} transform={`rotate(${i * 90} 50 50)`}>
            <path
              className={styles.petal}
              style={{ animationDelay: `${i * (intro ? 0.32 : 0.28)}s` }}
              d={PETAL}
              fill={fill ?? "none"}
              stroke={fill ? "none" : "var(--ink-3)"}
              strokeWidth={fill ? 0 : 3.5}
              strokeDasharray={fill ? undefined : "6 4"}
            />
          </g>
        );
      })}
      <path className={styles.core} d={CORE} fill="currentColor" />
    </svg>
  );
  if (!label) return svg;
  return (
    <span role="img" aria-label={label} style={{ display: "inline-flex" }}>
      {svg}
    </span>
  );
}

export function Lockup({ size = 26 }: { size?: number }) {
  return (
    <span className={styles.lockup}>
      <Mark size={size} />
      <span className={styles.word} style={{ fontSize: Math.round(size * 0.73) }}>
        consilith
      </span>
    </span>
  );
}
