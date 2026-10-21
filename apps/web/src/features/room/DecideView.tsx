import { type CSSProperties, useState } from "react";
import { ApiError } from "@/shared/http/client";
import { Icon, seatVar } from "@/shared/ui";
import s from "./decide.module.css";
import { nameOf } from "./describe";
import type { RoomConnection } from "./store";
import type { Proposal, RoomDoc, WarningFlag } from "./types";

/** Decide together: every open proposal and warning in the room. The lane owner decides with a
 * reason (it becomes a lesson); everyone else sees who it waits for. */
export function DecideView({ doc, me, conn }: { doc: RoomDoc; me: string; conn: RoomConnection }) {
  const open = Object.values(doc.proposals)
    .filter((p) => p.status === "awaiting-owner")
    .sort((a, b) => a.createdSeq - b.createdSeq);
  const warnings = Object.values(doc.warnings).filter((w) => !w.acknowledged);
  const decided = Object.values(doc.decisions)
    .sort((a, b) => b.seq - a.seq)
    .slice(0, 8);

  return (
    <div className={s.page}>
      <header>
        <h1 className={s.title}>
          <Icon name="decision" size={24} />
          Decide together
        </h1>
        <p className={s.sub}>
          Agents propose; the lane's owner decides. A reason is required and becomes a lesson the
          agent checks before it proposes again.
        </p>
      </header>

      <section aria-labelledby="open-h" className={s.section}>
        <h2 id="open-h" className={s.caps}>
          Open proposals · {open.length}
        </h2>
        {open.length ? (
          open.map((p) => <ProposalCard key={p.id} p={p} doc={doc} me={me} conn={conn} />)
        ) : (
          <p className={s.empty}>No proposals are waiting. Agents propose when a watch breaks.</p>
        )}
      </section>

      {warnings.length ? (
        <section aria-labelledby="warn-h" className={s.section}>
          <h2 id="warn-h" className={s.caps}>
            Warnings · {warnings.length}
          </h2>
          {warnings.map((w) => (
            <WarningCard key={w.id} w={w} doc={doc} me={me} conn={conn} />
          ))}
        </section>
      ) : null}

      {decided.length ? (
        <section aria-labelledby="dec-h" className={s.section}>
          <h2 id="dec-h" className={s.caps}>
            Decided
          </h2>
          <ul className={s.decided}>
            {decided.map((d) => (
              <li key={d.id}>
                <span className={d.accept ? s.acc : s.dec}>
                  {d.accept ? "accepted" : "declined"}
                </span>
                <span>
                  {nameOf(d.by, doc)}: {d.reason}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function ProposalCard({
  p,
  doc,
  me,
  conn,
}: {
  p: Proposal;
  doc: RoomDoc;
  me: string;
  conn: RoomConnection;
}) {
  const owner = p.laneOwner ?? doc.lanes[p.case]?.[p.targetLane] ?? null;
  const ownerP = owner ? doc.room.people[owner] : null;
  const author = doc.room.people[p.onBehalfOf];
  const mine = owner === me;
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(accept: boolean) {
    if (!reason.trim()) {
      setError("Give a reason; it becomes a lesson for the agent.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await conn.decide(p.id, accept, reason.trim());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The decision did not go through.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      className={s.card}
      style={{ "--c": seatVar(author?.color) } as CSSProperties}
      aria-label={`Proposal by ${p.author} in ${p.case}/${p.targetLane}`}
    >
      <header className={s.cardHead}>
        <Icon name="agent" size={22} accent={seatVar(author?.color)} />
        <div>
          <div className={s.strong}>
            {p.author} proposes in {p.case}/{p.targetLane}
          </div>
          <div className={s.small}>
            on behalf of {author?.name || p.onBehalfOf} · waits for{" "}
            {ownerP?.name || owner || "an owner"}
            {mine ? " (you)" : ""}
          </div>
        </div>
      </header>
      <p className={s.reasonText}>{p.reason}</p>
      <ul className={s.patch} aria-label="The change">
        {p.patch.map((op) => (
          <li key={`${op.op}${op.path}`} className={s.mono}>
            {op.op} {op.path.replace(`/twin/${p.case}/`, "")}
            {op.value !== undefined ? ` → ${formatValue(op.value)}` : ""}
          </li>
        ))}
      </ul>
      {p.evidence.length ? (
        <p className={s.small}>
          Evidence: {p.evidence.map((e) => doc.results[e]?.quantity ?? e).join(", ")}
        </p>
      ) : null}
      {mine ? (
        <div className={s.decide}>
          <label className={s.reasonField}>
            <span className="visually-hidden">Your reason</span>
            <input
              value={reason}
              placeholder="Your reason (it becomes a lesson)"
              onChange={(e) => {
                setReason(e.target.value);
                setError(null);
              }}
            />
          </label>
          <div className={s.btns}>
            <button type="button" className={s.accept} disabled={busy} onClick={() => decide(true)}>
              Accept
            </button>
            <button
              type="button"
              className={s.decline}
              disabled={busy}
              onClick={() => decide(false)}
            >
              Decline
            </button>
          </div>
        </div>
      ) : (
        <p className={s.small}>Only {ownerP?.name || owner || "the lane owner"} can decide this.</p>
      )}
      {error ? (
        <p className={s.bad} role="alert">
          {error}
        </p>
      ) : null}
    </article>
  );
}

function WarningCard({
  w,
  doc,
  me,
  conn,
}: {
  w: WarningFlag;
  doc: RoomDoc;
  me: string;
  conn: RoomConnection;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <article className={s.warn} aria-label={`Warning from ${w.author}`}>
      <Icon name="warning" size={18} />
      <div>
        <div className={s.strong}>
          {w.author} to {nameOf(w.to, doc)} · {w.case}/{w.lane}
        </div>
        <p className={s.reasonText}>{w.message}</p>
        {error ? (
          <p className={s.bad} role="alert">
            {error}
          </p>
        ) : null}
      </div>
      {w.to === me ? (
        <button
          type="button"
          className={s.decline}
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await conn.ackWarning(w.id);
            } catch (e) {
              setError(e instanceof ApiError ? e.message : "Not acknowledged.");
            } finally {
              setBusy(false);
            }
          }}
        >
          Acknowledge
        </button>
      ) : null}
    </article>
  );
}

function formatValue(v: unknown): string {
  if (v && typeof v === "object" && "v" in v && "unit" in v) {
    const n = v as { v: unknown; unit: unknown };
    return `${String(n.v)} ${String(n.unit)}`.trim();
  }
  return typeof v === "string" ? v : JSON.stringify(v);
}
