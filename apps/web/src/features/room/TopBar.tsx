import type { CSSProperties } from "react";
import { Link } from "react-router";
import { ThemeSwitch } from "@/shared/theme/ThemeSwitch";
import { Avatar, Icon, type IconName, Mark, seatVar } from "@/shared/ui";
import { agentModelLabel } from "./agentLabel";
import { studioIcon } from "./Deck";
import { agentOf } from "./describe";
import type { Connection } from "./feed";
import s from "./room.module.css";
import type { RoomDoc } from "./types";

const ROOM_AGENTS: { id: string; icon: IconName }[] = [
  { id: "conductor", icon: "conductor" },
  { id: "swarm", icon: "swarm" },
  { id: "solvers", icon: "solvers" },
  { id: "referee", icon: "referee" },
];

const CONN_TEXT: Record<Connection, string> = {
  connecting: "connecting",
  live: "live",
  reconnecting: "reconnecting",
  offline: "offline",
};

export function TopBar({
  doc,
  me,
  viewAs,
  onViewAs,
  connection,
  onInvite,
}: {
  doc: RoomDoc;
  me: string;
  viewAs: string;
  onViewAs: (id: string) => void;
  connection: Connection;
  onInvite: () => void;
}) {
  const people = Object.values(doc.room.people).sort((a, b) =>
    a.id === me ? -1 : b.id === me ? 1 : 0,
  );
  const open = Math.max(0, 4 - people.length);
  const seats = people.map((p) => seatVar(p.color));
  const active = doc.room.capabilities?.find((c) => c.id === doc.room.activeCase);
  const studio = active?.label ?? doc.room.activeCase;
  const roomAgentsOn = ROOM_AGENTS.filter((r) => doc.room.agents[r.id] || r.id === "referee");

  return (
    <header className={s.top}>
      <Link to="/" className={s.title} aria-label={`${doc.room.title}: back to your rooms`}>
        <Mark size={30} seats={seats} label={`Room mark: ${people.length} of 4 seats filled`} />
        <span className={s.titleText}>
          <span className={s.roomName}>{doc.room.title}</span>
          <span className={s.roomMeta}>
            <span className={s.liveDot} data-state={connection} aria-hidden />
            <span>{CONN_TEXT[connection]} ·</span>
            {active ? <Icon name={studioIcon(active)} size={13} /> : null}
            <span>
              {studio} · {people.length} of 4 seats
            </span>
          </span>
        </span>
      </Link>

      <section className={`${s.presence} ${s.floating}`} aria-label="Who is in the room">
        {people.map((p) => {
          const a = agentOf(doc, p.id);
          const status = a ? a.status : "idle";
          const idle = status === "idle";
          return (
            <span
              key={p.id}
              className={s.person}
              data-me={p.id === me || undefined}
              style={{ "--c": seatVar(p.color) } as CSSProperties}
              title={
                [agentModelLabel(doc, p.id), a?.statusDetail].filter(Boolean).join(" · ") ||
                undefined
              }
            >
              <Avatar name={p.name || p.id} color={p.color} size={24} />
              <Icon name={a?.brought ? "a2a" : "agent"} size={15} accent={seatVar(p.color)} />
              <span className={s.personText}>
                <span className={s.personName}>
                  {p.name || p.id}
                  {p.id === me ? " · you" : ""}
                </span>
                <span className={s.personStatus} data-idle={idle || undefined}>
                  {a?.brought ? (a.card?.name ?? "brought agent") : `${p.id}.agent`} · {status}
                </span>
              </span>
              <span className="visually-hidden">
                {p.name}, agent {status}
              </span>
            </span>
          );
        })}
        {Array.from({ length: open }, (_, i) => `open-${i}`).map((k) => (
          <button
            key={k}
            type="button"
            className={s.openSeat}
            aria-label="Open seat: invite someone"
            onClick={onInvite}
          >
            +
          </button>
        ))}
        <span className={s.sep} aria-hidden />
        <span className={s.roomAgents}>
          {roomAgentsOn.map((r) => {
            const a = doc.room.agents[r.id];
            return (
              <span
                key={r.id}
                className={s.ra}
                data-on={a ? true : undefined}
                title={`${r.id}${a ? ` · ${a.status}` : ""}`}
              >
                <Icon name={r.icon} size={16} />
              </span>
            );
          })}
          <span className={s.raLabel}>room agents</span>
          <span className="visually-hidden">Room agents: conductor, swarm, solvers, referee</span>
        </span>
      </section>

      <div className={s.actions}>
        {people.length > 1 ? (
          <fieldset className={`${s.viewAs} ${s.floating}`}>
            <legend className={s.viewAsLabel}>View as</legend>
            {people.map((p) => (
              <button
                key={p.id}
                type="button"
                className={s.viewer}
                aria-pressed={viewAs === p.id}
                style={{ "--c": seatVar(p.color) } as CSSProperties}
                onClick={() => onViewAs(p.id)}
              >
                {p.name || p.id}
              </button>
            ))}
          </fieldset>
        ) : null}
        <span className={s.hideOnPhone}>
          <ThemeSwitch />
        </span>
        <button
          type="button"
          className={`${s.pill} ${s.pillPrimary}`}
          aria-label="Invite someone"
          onClick={onInvite}
        >
          <Icon name="invite" size={16} />
          <span className={s.hideOnPhone}>Invite</span>
        </button>
      </div>
    </header>
  );
}
