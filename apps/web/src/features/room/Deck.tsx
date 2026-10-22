import { Link } from "react-router";
import type { Capability } from "@/shared/domain/disciplines";
import { studioIcon } from "@/shared/domain/studioIcon";
import { Avatar, Icon, type IconName, seatVar } from "@/shared/ui";
import s from "./room.module.css";

export type View =
  | { kind: "builtin"; id: "overview" | "decide" | "agents" }
  | { kind: "capability"; id: string };
const sameView = (a: View, b: View) => a.kind === b.kind && a.id === b.id;
function studioTools(capabilities: Capability[]): { view: View; icon: IconName; label: string }[] {
  return [
    { view: { kind: "builtin", id: "overview" }, icon: "room", label: "Room overview" },
    ...capabilities.map((studio) => ({
      view: { kind: "capability" as const, id: studio.id },
      icon: studioIcon(studio),
      label: studio.label,
    })),
  ];
}

export { studioIcon };

const WORK_TOOLS: { view: View; icon: IconName; label: string }[] = [
  { view: { kind: "builtin", id: "decide" }, icon: "decision", label: "Decide together" },
  { view: { kind: "builtin", id: "agents" }, icon: "wiring", label: "Agents at work" },
];

/** The floating deck: studios, shared work, history, and your agent. A bottom bar on phones. */
export function Deck({
  view,
  capabilities,
  onPlugins,
  onView,
  onLog,
  onLedger,
  onAgent,
  onChat,
  me,
  waiting,
}: {
  view: View;
  capabilities: Capability[];
  onPlugins: () => void;
  onView: (v: View) => void;
  onLog: () => void;
  onLedger: () => void;
  onAgent: () => void;
  onChat: () => void;
  me: { name: string; color: string };
  waiting: number;
}) {
  return (
    <nav className={`${s.deck} ${s.floating}`} aria-label="Room tools">
      {/* "+" opens a new room (Hesham, 2026-10-22); starting from a file or a sketch lives in
          the new-room page and in the studio itself. */}
      <Link
        to="/rooms/new"
        className={`${s.deckBtn} ${s.deckStart}`}
        data-tip="New room"
        aria-label="New room"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden>
          <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </Link>
      {studioTools(capabilities).map((t) => (
        <button
          key={`${t.view.kind}:${t.view.id}`}
          type="button"
          className={s.deckBtn}
          data-tip={t.label}
          aria-label={t.label}
          aria-current={sameView(t.view, view) ? "page" : undefined}
          disabled={!t.view}
          onClick={() => t.view && onView(t.view)}
        >
          <Icon name={t.icon} size={18} />
        </button>
      ))}
      <button
        type="button"
        className={s.deckBtn}
        aria-label="Room plugins"
        data-tip="Room plugins"
        onClick={onPlugins}
      >
        <Icon name="drawer" size={18} />
      </button>
      <span className={s.deckSep} aria-hidden />
      {WORK_TOOLS.map((t) => (
        <button
          key={`${t.view.kind}:${t.view.id}`}
          type="button"
          className={s.deckBtn}
          data-tip={t.label}
          aria-label={
            t.view.kind === "builtin" && t.view.id === "decide" && waiting
              ? `${t.label}, ${waiting} waiting on you`
              : t.label
          }
          aria-current={sameView(t.view, view) ? "page" : undefined}
          onClick={() => onView(t.view)}
        >
          <Icon name={t.icon} size={18} />
          {t.view.kind === "builtin" && t.view.id === "decide" && waiting ? (
            <span className={s.dot} aria-hidden />
          ) : null}
        </button>
      ))}
      <button
        type="button"
        className={s.deckBtn}
        data-tip="What just happened"
        aria-label="What just happened"
        onClick={onLog}
      >
        <Icon name="log" size={18} />
      </button>
      <button
        type="button"
        className={s.deckBtn}
        data-tip="Ledger"
        aria-label="Ledger"
        onClick={onLedger}
      >
        <Icon name="ledger" size={18} />
      </button>
      <span className={s.deckSep} aria-hidden />
      <button
        type="button"
        className={s.deckBtn}
        data-tip="Talk to your agent"
        aria-label="Talk to your agent"
        onClick={onChat}
      >
        <Icon name="room-agent" size={18} accent={seatVar(me.color)} />
      </button>
      <button
        type="button"
        className={`${s.deckBtn} ${s.deckMe}`}
        data-tip="Your agent: Consilith or your own"
        aria-label="Your agent"
        onClick={onAgent}
      >
        <Avatar name={me.name} color={me.color} size={30} />
        <span className={s.badge}>
          <Icon name="agent" size={13} accent={seatVar(me.color)} />
        </span>
      </button>
    </nav>
  );
}
