import { type CSSProperties, useEffect, useState } from "react";
import { Link } from "react-router";
import { intakeId } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Avatar, Icon, type IconName, Mark, seatVar } from "@/shared/ui";
import { type RoomSummary, roomsApi } from "../rooms/api";
import { agentModelLabel } from "./agentLabel";
import { studioIcon } from "./Deck";
import o from "./overview.module.css";
import { peerThreadsOf } from "./peer";
import type { RoomConnection } from "./store";
import type { CaseId, RoomDoc } from "./types";

const ROOM_AGENTS: { id: string; icon: IconName; label: string }[] = [
  { id: "conductor", icon: "conductor", label: "Conductor" },
  { id: "swarm", icon: "swarm", label: "Swarm" },
  { id: "solvers", icon: "solvers", label: "Solvers" },
  { id: "referee", icon: "referee", label: "Referee" },
];

type Point = { x: number; y: number };
/** A point on an ellipse around the centre, in percent of the stage; 0 degrees is straight up. */
function around(deg: number, rx: number, ry: number): Point {
  const r = ((deg - 90) * Math.PI) / 180;
  return { x: 50 + rx * Math.cos(r), y: 50 + ry * Math.sin(r) };
}

/** The room at a glance: who is in it and their agents around the room, the room's own agents
 * inside, the agents talking right now, the lanes and who owns them, and your other rooms. */
export function Overview({
  doc,
  me,
  conn,
  caseId,
  onStart,
}: {
  doc: RoomDoc;
  me: string;
  conn: RoomConnection;
  caseId: CaseId;
  onStart: (kind: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  useEffect(() => {
    roomsApi.list().then(setRooms, () => setRooms([]));
  }, []);

  const owners = doc.lanes[caseId] ?? {};
  const capability = doc.room.capabilities?.find((c) => c.id === caseId);
  const lanes = (capability?.lanes ?? []).map((l) => ({
    ...l,
    role: l.defaultRole ?? l.default_role ?? l.role,
  }));
  const owned = lanes.filter((l) => owners[l.id]).length;
  const myRole = doc.room.people[me]?.role;
  const people = Object.values(doc.room.people).sort((a, b) =>
    a.id === me ? -1 : b.id === me ? 1 : 0,
  );
  const seats = people.map((p) => seatVar(p.color));

  // The constellation: people on the outer ring, their agents between them and the room, the
  // room agents on the inner ring.
  const step = 360 / Math.max(people.length, 1);
  const nodes = people.map((p, i) => {
    const deg = -60 + i * step;
    return {
      p,
      person: around(deg, 44, 40),
      agent: around(deg, 23, 21),
      color: seatVar(p.color),
      label: agentModelLabel(doc, p.id),
    };
  });
  const agentAt = new Map(nodes.map((n) => [`${n.p.id}.agent`, n]));
  const inner = ROOM_AGENTS.filter((r) => doc.room.agents[r.id] || r.id === "referee").map(
    (r, i, all) => ({ ...r, at: around(45 + i * (360 / all.length), 12, 12) }),
  );
  const talking = peerThreadsOf(doc).filter((t) => t.status === "open");

  async function act(lane: string, kind: "claim" | "release") {
    setBusy(lane);
    setError(null);
    try {
      await (kind === "claim" ? conn.claim(caseId, lane) : conn.release(caseId, lane));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }

  const others = rooms.filter((r) => r.id !== doc.room.id && !r.waiting);

  return (
    <div className={o.page}>
      <header className={o.head}>
        <p className={o.eyebrow}>Room overview</p>
        <h1 className={o.title}>{doc.room.title}</h1>
        <p className={o.sub}>
          {people.length} of 4 seats · {nodes.length} personal agent
          {nodes.length === 1 ? "" : "s"} · {talking.length} conversation
          {talking.length === 1 ? "" : "s"} between agents
        </p>
      </header>

      <div className={o.grid}>
        <section className={o.stage} aria-label="People, agents and the room">
          <svg className={o.edges} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
            {nodes.map((n) => (
              <g key={n.p.id} style={{ "--c": n.color } as CSSProperties}>
                <line
                  className={o.tie}
                  x1={n.person.x}
                  y1={n.person.y}
                  x2={n.agent.x}
                  y2={n.agent.y}
                />
                <line className={o.spoke} x1={n.agent.x} y1={n.agent.y} x2={50} y2={50} />
              </g>
            ))}
            {talking.map((t) => {
              const [a, b] = t.agents.map((id) => agentAt.get(id));
              if (!a || !b) return null;
              // Bow the arc away from the centre so it does not run through the room mark.
              const mx = (a.agent.x + b.agent.x) / 2;
              const my = (a.agent.y + b.agent.y) / 2;
              const cx = mx + (mx - 50) * 0.9;
              const cy = my + (my - 50) * 0.9;
              return (
                <path
                  key={t.id}
                  className={o.talk}
                  d={`M ${a.agent.x} ${a.agent.y} Q ${cx} ${cy} ${b.agent.x} ${b.agent.y}`}
                  data-live={t.pending ? "true" : undefined}
                />
              );
            })}
          </svg>

          <div className={o.centre}>
            <Mark size={44} seats={seats} label={`${doc.room.title}: ${people.length} seats`} />
          </div>
          {inner.map((r) => (
            <div
              key={r.id}
              className={o.roomAgent}
              style={{ left: `${r.at.x}%`, top: `${r.at.y}%` }}
              title={`${r.label}: a room agent, owned by nobody`}
            >
              <Icon name={r.icon} size={14} />
            </div>
          ))}
          {nodes.map((n) => (
            <div key={n.p.id} style={{ "--c": n.color } as CSSProperties}>
              <div
                className={o.agent}
                style={{ left: `${n.agent.x}%`, top: `${n.agent.y}%` }}
                title={n.label}
              >
                <span className={o.agentHead}>
                  <Icon name="agent" size={14} accent={n.color} />
                  <span className={o.agentName}>{n.p.id}.agent</span>
                </span>
                {n.label ? <span className={o.model}>{n.label}</span> : null}
              </div>
              <div className={o.person} style={{ left: `${n.person.x}%`, top: `${n.person.y}%` }}>
                <Avatar name={n.p.name || n.p.id} color={n.p.color} size={28} />
                <span className={o.personName}>
                  {n.p.name || n.p.id}
                  {n.p.id === me ? " · you" : ""}
                </span>
              </div>
            </div>
          ))}
          {talking.length ? (
            <p className={o.legend}>
              <span className={o.legendLine} aria-hidden /> agents talking now
            </p>
          ) : null}
        </section>

        <section className={o.lanes} aria-labelledby="lanes-h">
          <h2 id="lanes-h" className={o.caps}>
            {capability ? <Icon name={studioIcon(capability)} size={14} /> : null}
            {capability?.label ?? caseId}
          </h2>
          <p className={o.lanesNote}>
            {owned === lanes.length
              ? "Every lane has an owner."
              : `${lanes.length - owned} of ${lanes.length} lanes open · a run waits for every owner`}
          </p>
          <ul className={o.laneList}>
            {lanes.map((l) => {
              const owner = owners[l.id] ?? null;
              const p = owner ? doc.room.people[owner] : null;
              const mine = owner === me;
              return (
                <li
                  key={l.id}
                  className={o.lane}
                  data-open={owner ? undefined : true}
                  style={p ? ({ "--c": seatVar(p.color) } as CSSProperties) : undefined}
                >
                  <span className={o.laneName}>{l.label || l.id}</span>
                  <span className={o.laneOwner}>
                    {p ? (
                      <>
                        <Avatar name={p.name || p.id} color={p.color} size={16} />
                        {mine ? "you" : p.name || p.id}
                      </>
                    ) : (
                      <>open{l.role === myRole ? " · fits your role" : ""}</>
                    )}
                  </span>
                  {!owner ? (
                    <button
                      type="button"
                      className={o.laneBtn}
                      disabled={busy !== null}
                      onClick={() => act(l.id, "claim")}
                      aria-label={`Claim ${l.id}`}
                    >
                      {busy === l.id ? "Claiming…" : "Claim"}
                    </button>
                  ) : mine ? (
                    <button
                      type="button"
                      className={o.laneBtn}
                      disabled={busy !== null}
                      onClick={() => act(l.id, "release")}
                      aria-label={`Release ${l.id}`}
                    >
                      {busy === l.id ? "Releasing…" : "Release"}
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
          {error ? (
            <p className={o.bad} role="alert">
              {error}
            </p>
          ) : null}
          <div className={o.starts}>
            {Object.entries(capability?.intakeBindings ?? {}).map(([kind, binding]) => (
              <button
                key={kind}
                type="button"
                className={o.start}
                onClick={() => onStart(intakeId(caseId, kind))}
              >
                {binding.label ?? kind}
              </button>
            ))}
            <button type="button" className={o.start} onClick={() => onStart("words")}>
              Describe your request
            </button>
          </div>
        </section>
      </div>

      {others.length ? (
        <section className={o.rooms} aria-labelledby="rooms-h">
          <h2 id="rooms-h" className={o.caps}>
            Your other rooms
          </h2>
          <ul className={o.bubbles}>
            {others.map((r) => (
              <li key={r.id}>
                <Link to={`/rooms/${encodeURIComponent(r.id)}`} className={o.bubble}>
                  <Mark
                    size={34}
                    seats={r.seats.map((x) => seatVar(x.color))}
                    label={`${r.title}: ${r.seats.length} seats`}
                  />
                  <span className={o.bubbleText}>
                    <span className={o.bubbleTitle}>{r.title}</span>
                    <span className={o.bubbleMeta}>
                      {r.seats.length} {r.seats.length === 1 ? "person" : "people"} and their agents
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
