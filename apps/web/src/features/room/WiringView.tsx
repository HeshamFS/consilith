import { type CSSProperties, useState } from "react";
import { Icon, type IconName, seatVar } from "@/shared/ui";
import { agentModelLabel } from "./agentLabel";
import { ModelChip } from "./PeerChat";
import { PeerThreads } from "./PeerThreads";
import { peerThreadsOf } from "./peer";
import type { RoomDoc, ToolCall, WiringEvent } from "./types";
import s from "./wiring.module.css";

const ROOM_ICON: Record<string, IconName> = {
  conductor: "conductor",
  swarm: "swarm",
  solvers: "solvers",
  referee: "referee",
};

/** The protocol messages shown before "Show more". */
const FIRST_ROWS = 10;

/** Agents at work: one line on who is here, the agents' threads, every agent with its status, and
 * the protocol messages between them as they happen (journal wiring: A2A between agents, MCP to
 * the solvers, A2UI to people). */
export function WiringView({
  doc,
  wiring,
  tools,
  onAgent,
  me,
  thread,
  onOpenLane,
}: {
  doc: RoomDoc;
  wiring: WiringEvent[];
  tools: Record<string, ToolCall>;
  onAgent: (person: string) => void;
  /** The signed-in person: shows their peer switches and their side of each thread. */
  me?: string | undefined;
  /** A thread to open first (the chat strip links here). */
  thread?: string | undefined;
  /** Where a proposal card in a thread goes: the lane it targets. */
  onOpenLane?: ((caseId: string, lane: string) => void) | undefined;
}) {
  const [more, setMore] = useState(false);
  const agents = Object.values(doc.room.agents);
  const personal = agents.filter((a) => a.kind === "personal");
  const room = agents.filter((a) => a.kind === "room");
  const people = Object.keys(doc.room.people).length;
  const open = peerThreadsOf(doc).filter((t) => t.status === "open").length;
  const recent = [...wiring].reverse();
  const shown = more ? recent.slice(0, 60) : recent.slice(0, FIRST_ROWS);
  const calls = Object.values(tools).slice(-6).reverse();

  return (
    <div className={s.page}>
      <header className={s.head}>
        <h1 className={s.title}>
          <Icon name="wiring" size={18} />
          Agents at work
        </h1>
        <p className={s.sub}>
          {people} {people === 1 ? "person" : "people"} · {personal.length} personal agent
          {personal.length === 1 ? "" : "s"} · {room.length} room agent
          {room.length === 1 ? "" : "s"} · {open} open thread{open === 1 ? "" : "s"}
        </p>
      </header>

      <PeerThreads doc={doc} me={me} initialThread={thread} onOpenLane={onOpenLane} />

      <div className={s.cols}>
        <section className={s.agents} aria-labelledby="ag-h">
          <h2 id="ag-h" className={s.caps}>
            Personal agents
          </h2>
          {personal.map((a) => {
            if (a.kind !== "personal") return null;
            const p = doc.room.people[a.owner];
            return (
              <button
                key={a.id}
                type="button"
                className={s.agent}
                style={{ "--c": seatVar(p?.color) } as CSSProperties}
                onClick={() => onAgent(a.owner)}
              >
                <Icon name={a.brought ? "a2a" : "agent"} size={16} accent={seatVar(p?.color)} />
                <span className={s.agentText}>
                  <span className={s.strong}>{a.brought ? (a.card?.name ?? a.id) : a.id}</span>
                  <span className={s.small}>
                    {p?.name || a.owner} · {a.config.autonomy.replace(/_/g, " ")} ·{" "}
                    {a.config.skills.length} skills
                  </span>
                  <ModelChip label={agentModelLabel(doc, a.id)} />
                </span>
                <span className={s.status} data-s={a.status}>
                  {a.status}
                </span>
              </button>
            );
          })}
          <h2 className={s.caps}>Room agents</h2>
          <div className={s.roomAgents}>
            {room.map((a) => (
              <div key={a.id} className={s.roomAgent} title={a.statusDetail || "owned by nobody"}>
                <Icon name={ROOM_ICON[a.id] ?? "room-agent"} size={14} />
                <span className={s.strong}>{a.id}</span>
                <span className={s.status} data-s={a.status}>
                  {a.status}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className={s.stream} aria-labelledby="wire-h">
          <h2 id="wire-h" className={s.caps}>
            Messages · newest first
          </h2>
          {shown.length ? (
            <ol className={s.list}>
              {shown.map((w) => (
                <li key={w.journalSeq}>
                  <span className={s.proto} data-p={w.protocol}>
                    {w.protocol}
                  </span>
                  <span className={s.flow}>
                    <b>{w.sender}</b> → <b>{w.receiver}</b>
                  </span>
                  <span className={s.kind}>{w.kind}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className={s.empty}>
              Quiet so far. Messages appear here when an agent proposes, a solve runs or a card is
              fetched.
            </p>
          )}
          {recent.length > FIRST_ROWS ? (
            <button type="button" className={s.more} onClick={() => setMore((m) => !m)}>
              {more ? "Show fewer" : `Show more (${recent.length - FIRST_ROWS})`}
            </button>
          ) : null}
          {calls.length ? (
            <>
              <h2 className={s.caps}>Tool calls</h2>
              <ol className={s.list}>
                {calls.map((t) => (
                  <li key={t.id}>
                    <span className={s.proto} data-p="MCP">
                      MCP
                    </span>
                    <span className={s.flow}>{t.name || t.id}</span>
                    <span className={s.kind}>
                      {t.done ? (t.result ? "returned" : "done") : "running"}
                    </span>
                  </li>
                ))}
              </ol>
            </>
          ) : null}
        </section>
      </div>
    </div>
  );
}
