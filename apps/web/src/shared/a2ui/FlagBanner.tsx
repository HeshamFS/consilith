import { useId, useState } from "react";
import { Icon, type StampKind } from "@/shared/ui";
import s from "./chart.module.css";

/** What a chart or field says about the data it draws when the data is not plainly good. One
 * flag per reason, worst first. A single reason is one line; several share one banner that opens
 * to list each, so a card at 460 px keeps its height for the picture. */

export type FlagSource = {
  name: string;
  stamp?: StampKind | undefined;
  status?: string | undefined;
};
export type Flag = {
  tone: "blocked" | "warning" | "checking" | "stale";
  names: string[];
  text: string;
};

const SHORT: Record<Flag["tone"], string> = {
  blocked: "Blocked",
  warning: "Warning",
  checking: "Checking",
  stale: "Not current",
};

/** What the banner says, worst first: blocked, warning, checking, then not current. */
export function seriesFlags(series: readonly FlagSource[]): Flag[] {
  const flags: Flag[] = [];
  const names = (pick: (x: (typeof series)[number]) => boolean) =>
    series.filter(pick).map((x) => x.name);
  const blocked = names((x) => x.stamp === "blocked");
  if (blocked.length)
    flags.push({
      tone: "blocked",
      names: blocked,
      text: "Blocked: no approved checker has passed this data.",
    });
  const warning = names((x) => x.stamp === "warning");
  if (warning.length)
    flags.push({ tone: "warning", names: warning, text: "Warning raised on this data." });
  const checking = names((x) => x.stamp === "checking");
  if (checking.length)
    flags.push({ tone: "checking", names: checking, text: "Still being checked." });
  const stale = names((x) => !!x.status && x.status !== "current");
  if (stale.length)
    flags.push({
      tone: "stale",
      names: stale,
      text: "Not current: an input changed or a newer result replaced it.",
    });
  return flags;
}

export function FlagBanner({ flags }: { flags: Flag[] }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const first = flags[0];
  if (!first) return null;
  const icon = <Icon name={first.tone === "blocked" ? "blocked" : "warning"} size={15} />;
  if (flags.length === 1)
    return (
      <p className={s.banner} data-tone={first.tone} role="status">
        {icon}
        <span>
          <strong>{first.names.join(", ")}</strong> {first.text}
        </span>
      </p>
    );
  const affected = new Set(flags.flatMap((f) => f.names)).size;
  return (
    <div className={s.banner} data-tone={first.tone} data-multi="true" role="status">
      {icon}
      <div className={s.bannerBody}>
        <div className={s.bannerLine}>
          <span>
            <strong>{flags.map((f) => SHORT[f.tone]).join(" · ")}</strong>
            <span className={s.bannerCount}>
              {" "}
              on {affected} {affected === 1 ? "item" : "items"}
            </span>
          </span>
          <button
            type="button"
            className={s.bannerToggle}
            aria-expanded={open}
            aria-controls={id}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Hide" : "Details"}
          </button>
        </div>
        <ul id={id} className={s.bannerList} hidden={!open}>
          {flags.map((f) => (
            <li key={f.tone}>
              <strong>{f.names.join(", ")}</strong> {f.text}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
