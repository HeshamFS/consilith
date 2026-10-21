import { type CSSProperties, useEffect, useRef, useState } from "react";
import { Icon, seatVar } from "@/shared/ui";
import s from "./arrival.module.css";
import type { PersonalAgent, RoomDoc } from "./types";

const PHASES = ["skeleton", "materialising", "live"] as const;

/** When a person joins while you are in the room, their agent arrives on screen: its card
 * skeleton, then its skills and tools as the room lists them, then live. The items are the
 * agent's real config; only the pacing is choreography. People already seated on first load
 * do not arrive again. */
export function Arrival({ doc, me }: { doc: RoomDoc; me: string }) {
  const known = useRef<Set<string> | null>(null);
  const [agent, setAgent] = useState<PersonalAgent | null>(null);
  const [phase, setPhase] = useState(0);
  const [shown, setShown] = useState(0);

  useEffect(() => {
    const ids = Object.keys(doc.room.people);
    if (!known.current) {
      known.current = new Set(ids);
      return;
    }
    const fresh = ids.find((id) => !known.current?.has(id) && id !== me);
    for (const id of ids) known.current.add(id);
    if (!fresh) return;
    const a = doc.room.agents[`${fresh}.agent`];
    if (a && a.kind === "personal") {
      setAgent(a);
      setPhase(0);
      setShown(0);
    }
  }, [doc.room.people, doc.room.agents, me]);

  const items = agent ? [...agent.config.skills, ...agent.config.toolsAllowed] : [];
  useEffect(() => {
    if (!agent) return;
    const n = agent.config.skills.length + agent.config.toolsAllowed.length;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setPhase(2);
      setShown(n);
      const t = setTimeout(() => setAgent(null), 2500);
      return () => clearTimeout(t);
    }
    const timers: ReturnType<typeof setTimeout>[] = [];
    timers.push(setTimeout(() => setPhase(1), 400));
    for (let i = 0; i < n; i++) {
      timers.push(setTimeout(() => setShown(i + 1), 520 + i * 110));
    }
    const live = 620 + n * 110;
    timers.push(setTimeout(() => setPhase(2), live));
    timers.push(setTimeout(() => setAgent(null), live + 2200));
    return () => {
      for (const t of timers) clearTimeout(t);
    };
  }, [agent]);

  if (!agent) return null;
  const owner = doc.room.people[agent.owner];
  const c = seatVar(owner?.color);
  return (
    <section
      className={s.dock}
      style={{ "--c": c } as CSSProperties}
      aria-label={`${agent.id} arriving`}
      aria-live="polite"
    >
      <div className={s.head}>
        <Icon name={agent.brought ? "a2a" : "agent"} size={22} accent={c} />
        <span className={s.name}>{agent.brought ? (agent.card?.name ?? agent.id) : agent.id}</span>
        <span className={s.verb}>
          {phase < 2
            ? `arriving with ${owner?.name || agent.owner}`
            : "live · Agent Card published"}
        </span>
      </div>
      <ol className={s.phases}>
        {PHASES.map((p, i) => (
          <li key={p} data-on={i <= phase || undefined}>
            <span className={s.bar} />
            {p}
          </li>
        ))}
      </ol>
      <ul className={s.items}>
        {items.slice(0, shown).map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </section>
  );
}
