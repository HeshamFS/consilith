import type { CSSProperties } from "react";
import { Icon, type IconName, seatVar } from "@/shared/ui";
import type { RoomDoc, ToolCall, WiringEvent } from "./types";
import s from "./wiring.module.css";

const ROOM_ICON: Record<string, IconName> = {
  conductor: "conductor",
  swarm: "swarm",
  solvers: "solvers",
  referee: "referee",
};

/** Agents at work: every agent in the room with its status, and the protocol messages between
 * them as they happen (journal wiring: A2A between agents, MCP to the solvers, A2UI to people). */
export function WiringView({
  doc,
  wiring,
  tools,
  onAgent,
}: {
  doc: RoomDoc;
  wiring: WiringEvent[];
  tools: Record<string, ToolCall>;
  onAgent: (person: string) => void;
}) {
  const agents = Object.values(doc.room.agents);
  const personal = agents.filter((a) => a.kind === "personal");
  const room = agents.filter((a) => a.kind === "room");
  const recent = [...wiring].reverse().slice(0, 24);
  const calls = Object.values(tools).slice(-8).reverse();

  return (
    <div className={s.page}>
      <header>
        <h1 className={s.title}>
          <Icon name="wiring" size={24} />
          Agents at work
        </h1>
        <p className={s.sub}>
          Personal agents work for one person each; room agents are owned by nobody. Every message
          between them is journaled with the protocol that carried it.
        </p>
      </header>
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
                <Icon name={a.brought ? "a2a" : "agent"} size={26} accent={seatVar(p?.color)} />
                <span className={s.agentText}>
                  <span className={s.strong}>{a.brought ? (a.card?.name ?? a.id) : a.id}</span>
                  <span className={s.small}>
                    {p?.name || a.owner} · {a.config.autonomy.replace(/_/g, " ")} ·{" "}
                    {a.config.skills.length} skills
                  </span>
                </span>
                <span className={s.status} data-s={a.status}>
                  {a.status}
                </span>
              </button>
            );
          })}
          <h2 className={s.caps}>Room agents</h2>
          {room.map((a) => (
            <div key={a.id} className={s.agent}>
              <Icon name={ROOM_ICON[a.id] ?? "room-agent"} size={26} />
              <span className={s.agentText}>
                <span className={s.strong}>{a.id}</span>
                <span className={s.small}>{a.statusDetail || "owned by nobody"}</span>
              </span>
              <span className={s.status} data-s={a.status}>
                {a.status}
              </span>
            </div>
          ))}
        </section>

        <section className={s.stream} aria-labelledby="wire-h">
          <h2 id="wire-h" className={s.caps}>
            Messages · newest first
          </h2>
          {recent.length ? (
            <ol className={s.list}>
              {recent.map((w) => (
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
