import type { Capability } from "@/shared/domain/disciplines";
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
      icon: "solvers" as IconName,
      label: studio.label,
    })),
  ];
}

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
  onStart,
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
  onStart: () => void;
  onLog: () => void;
  onLedger: () => void;
  onAgent: () => void;
  onChat: () => void;
  me: { name: string; color: string };
  waiting: number;
}) {
  return (
    <nav className={`${s.deck} ${s.floating}`} aria-label="Room tools">
      <button
        type="button"
        className={`${s.deckBtn} ${s.deckStart}`}
        data-tip="Start from anything"
        aria-label="Start from anything"
        onClick={onStart}
      >
        <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
          <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
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
          <Icon name={t.icon} size={22} />
        </button>
      ))}
      <button
        type="button"
        className={s.deckBtn}
        aria-label="Room plugins"
        data-tip="Room plugins"
        onClick={onPlugins}
      >
        <Icon name="drawer" size={22} />
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
          <Icon name={t.icon} size={22} />
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
        <Icon name="log" size={22} />
      </button>
      <button
        type="button"
        className={s.deckBtn}
        data-tip="Ledger"
        aria-label="Ledger"
        onClick={onLedger}
      >
        <Icon name="ledger" size={22} />
      </button>
      <span className={s.deckSep} aria-hidden />
      <button
        type="button"
        className={s.deckBtn}
        data-tip="Talk to your agent"
        aria-label="Talk to your agent"
        onClick={onChat}
      >
        <Icon name="room-agent" size={22} accent={seatVar(me.color)} />
      </button>
      <button
        type="button"
        className={`${s.deckBtn} ${s.deckMe}`}
        data-tip="Your agent: Consilith or your own"
        aria-label="Your agent"
        onClick={onAgent}
      >
        <Avatar name={me.name} color={me.color} size={36} />
        <span className={s.badge}>
          <Icon name="agent" size={13} accent={seatVar(me.color)} />
        </span>
      </button>
    </nav>
  );
}
