import { Icon } from "@/shared/ui";
import { ago, describeLog } from "./describe";
import s from "./room.module.css";
import type { LogEvent, RoomDoc } from "./types";

export function waitingOnMe(doc: RoomDoc, me: string): number {
  const props = Object.values(doc.proposals).filter(
    (p) =>
      p.status === "awaiting-owner" && (p.laneOwner ?? doc.lanes[p.case]?.[p.targetLane]) === me,
  ).length;
  const warns = Object.values(doc.warnings).filter((w) => w.to === me && !w.acknowledged).length;
  return props + warns;
}

/** Bottom docks: what the room can run (left) and what waits on you, with the newest event. */
export function Docks({
  doc,
  me,
  log,
  onWaiting,
  onLog,
}: {
  doc: RoomDoc;
  me: string;
  log: LogEvent[];
  onWaiting: () => void;
  onLog: () => void;
}) {
  const waiting = waitingOnMe(doc, me);
  const running = Object.values(doc.runs).filter((r) => r.status === "running").length;
  const last = log[log.length - 1];
  const solvers = doc.room.agents.solvers;
  return (
    <>
      <div className={s.status}>
        <span className={`${s.statusChip} ${s.floating}`}>
          <Icon name="solvers" size={14} />
          {running
            ? `${running} solve${running === 1 ? "" : "s"} running`
            : solvers
              ? `solvers ${solvers.status}`
              : "solvers not connected"}
        </span>
      </div>
      <section className={`${s.waiting} ${s.floating}`} aria-label="Waiting on you">
        <button type="button" className={s.waitingHead} onClick={onWaiting}>
          <Icon name="proposal" size={14} />
          Waiting on you <span className={s.count}>{waiting}</span>
        </button>
        {last ? (
          <button type="button" className={`${s.waitingLast} ${s.plain}`} onClick={onLog}>
            <Icon name="log" size={13} />
            <span>{describeLog(last, doc)}</span>
            <span className={s.ago}>{ago(last.ts)}</span>
          </button>
        ) : null}
      </section>
    </>
  );
}
