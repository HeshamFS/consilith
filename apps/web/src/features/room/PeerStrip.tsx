import type { CSSProperties } from "react";
import { seatVar } from "@/shared/ui";
import { HopMeter } from "./PeerThreads";
import { hopCounter, otherOf, type PeerThread, stripLine, threadState } from "./peer";
import s from "./peer.module.css";
import type { PeerThreadIndex, RoomDoc } from "./types";

/** The one-line strip in the chat sheet: "Your agent talked with colleague.agent (3) · open".
 * With no threads it renders nothing, so the chat looks the same until a thread exists. Bodies
 * are never carried here, only the index (id, participants, count and states).
 *
 * Without `me` it keeps the plain F4 form, `p2 · 3`. With `me` and the room document it shows
 * the seat colours of the two agents, a live dot while the other agent is replying, and the hop
 * counter against the cap. It takes one line per thread, which fits the 460 px chat sheet. */
export function PeerStrip({
  threads = [],
  me,
  doc,
  onOpen,
}: {
  threads?: PeerThread[] | PeerThreadIndex[];
  me?: string | null | undefined;
  doc?: RoomDoc | null | undefined;
  onOpen?: ((id: string) => void) | undefined;
}) {
  const open = (threads as PeerThread[]).filter((t) => t.status === "open");
  if (!open.length) return null;
  return (
    <nav className={s.strip} aria-label="Peer threads">
      {open.map((t) => {
        if (!me) {
          return (
            <button
              key={t.id}
              type="button"
              className={`${s.chip} ${s.chipLegacy}`}
              onClick={() => onOpen?.(t.id)}
            >
              {stripLine(t)}
            </button>
          );
        }
        const state = threadState(t);
        const mine = t.participants.includes(me);
        const other = otherOf(t, me);
        const colour = (person: string) =>
          ({ "--c": seatVar(doc?.room.people[person]?.color) }) as CSSProperties;
        const hops = hopCounter(t);
        const line = mine
          ? stripLine(t, me, doc)
          : `${t.participants.join(" and ")} (${t.count}) · ${state === "quiet" ? "open" : state}`;
        return (
          <button
            key={t.id}
            type="button"
            className={s.chip}
            data-state={state}
            onClick={() => onOpen?.(t.id)}
            aria-label={`${line}. Open the thread.`}
          >
            <span className={s.pair} aria-hidden>
              <i style={colour(mine ? me : (t.participants[0] ?? ""))} />
              <i style={colour(other)} />
            </span>
            <span className={s.chipText}>{line}</span>
            <span className={s.chipTail}>
              {state === "replying" ? <span className={s.live} aria-hidden /> : null}
              <HopMeter thread={t} />
              <span className="visually-hidden">
                hop {hops.value} of {hops.max}
              </span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}
