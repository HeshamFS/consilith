import { ResultValue } from "@/shared/a2ui/ResultValue";
import { Icon, Stamp } from "@/shared/ui";
import { ago, describeLog } from "./describe";
import { PeerThreads } from "./PeerThreads";
import s from "./room.module.css";
import { Sheet } from "./Sheet";
import type { LogEvent, RoomDoc, WiringEvent } from "./types";

export function LogSheet({
  doc,
  log,
  wiring,
  onClose,
}: {
  doc: RoomDoc;
  log: LogEvent[];
  wiring: WiringEvent[];
  onClose: () => void;
}) {
  const rows = [...log].reverse();
  return (
    <Sheet
      title="What just happened"
      sub="Every change in the room, newest first, with the protocol that carried it"
      icon={<Icon name="log" size={20} />}
      narrow
      onClose={onClose}
    >
      {rows.length ? (
        <ul className={s.logList}>
          {rows.map((e) => (
            <li key={e.seq}>
              <span className={s.ago}>{ago(e.ts)}</span>
              <span className={s.proto} data-p={e.protocol}>
                {e.protocol}
              </span>
              <span>{describeLog(e, doc)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className={s.empty}>Nothing yet since you opened the room.</p>
      )}
      {wiring.length ? (
        <>
          <h3 className={s.caps} style={{ marginTop: 24 }}>
            Wiring · {wiring.length} messages between agents
          </h3>
          <ul className={s.logList}>
            {[...wiring]
              .reverse()
              .slice(0, 40)
              .map((w) => (
                <li key={w.journalSeq}>
                  <span className={s.ago}>#{w.journalSeq}</span>
                  <span className={s.proto} data-p={w.protocol}>
                    {w.protocol}
                  </span>
                  <span>
                    {w.sender} → {w.receiver} · {w.kind}
                  </span>
                </li>
              ))}
          </ul>
        </>
      ) : null}
    </Sheet>
  );
}

/** The agent threads in a sheet, for the strip in the chat to open: the 460 px width of a narrow
 * sheet is the size the thread is designed for. */
export function ThreadSheet({
  doc,
  me,
  thread,
  onOpenLane,
  onClose,
}: {
  doc: RoomDoc;
  me?: string | undefined;
  thread?: string | undefined;
  onOpenLane?: ((caseId: string, lane: string) => void) | undefined;
  onClose: () => void;
}) {
  return (
    <Sheet
      title="Agent threads"
      sub="Your agents talk through the conductor. Everyone in the room can read this."
      icon={<Icon name="a2a" size={20} />}
      narrow
      onClose={onClose}
    >
      <PeerThreads
        doc={doc}
        me={me}
        initialThread={thread}
        onOpenLane={onOpenLane}
        showSettings={false}
      />
    </Sheet>
  );
}

export function LedgerSheet({ doc, onClose }: { doc: RoomDoc; onClose: () => void }) {
  const entries = Object.values(doc.ledger).sort((a, b) => b.ts - a.ts);
  return (
    <Sheet
      title="Ledger"
      sub="Results the referee checked and signed. Only these carry a verified stamp."
      icon={<Icon name="ledger" size={20} />}
      narrow
      onClose={onClose}
    >
      {entries.length ? (
        <ul className={s.logList}>
          {entries.map((e) => (
            <li key={e.id}>
              <span className={s.ago}>{ago(e.ts)}</span>
              <Stamp kind={e.subject.stamp} iconOnly />
              <div>
                {e.subject.quantity} · {e.subject.method}
                <ResultValue result={e.subject} showStamp={false} />
                {e.current === false ? " · superseded" : ""}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className={s.empty}>
          No signed results yet. The first solve that passes its checks lands here.
        </p>
      )}
    </Sheet>
  );
}
