import { type CSSProperties, useState } from "react";
import { intakeId } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Avatar, Icon, seatVar } from "@/shared/ui";
import s from "./room.module.css";
import type { RoomConnection } from "./store";
import type { CaseId, RoomDoc } from "./types";

/** The room at a glance: the active studio's lanes, who owns each, and where to start. */
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
  const owners = doc.lanes[caseId] ?? {};
  const capability = doc.room.capabilities?.find((c) => c.id === caseId);
  const lanes = (capability?.lanes ?? []).map((l) => ({
    ...l,
    role: l.defaultRole ?? l.default_role ?? l.role,
  }));
  const owned = lanes.filter((l) => owners[l.id]).length;
  const myRole = doc.room.people[me]?.role;

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

  return (
    <div className={s.center}>
      <section className={`${s.lanesCard} ${s.floating}`} aria-labelledby="lanes-h">
        <div className={s.cardHead}>
          <h2 id="lanes-h" className={s.caps}>
            <Icon name="solvers" size={16} />
            Shared twin · {capability?.label ?? caseId} lanes
          </h2>
          {owned === lanes.length ? (
            <span className={s.ownedOk}>
              <Icon name="verified" size={15} />
              every lane owned
            </span>
          ) : (
            <span className={s.ownedWait}>
              {lanes.length - owned} of {lanes.length} lanes open · solves wait for every owner
            </span>
          )}
        </div>
        <ul className={s.lanes}>
          {lanes.map((l) => {
            const owner = owners[l.id] ?? null;
            const p = owner ? doc.room.people[owner] : null;
            const mine = owner === me;
            return (
              <li
                key={l.id}
                className={s.lane}
                data-open={owner ? undefined : true}
                style={p ? ({ "--c": seatVar(p.color) } as CSSProperties) : undefined}
              >
                <div className={s.laneHead}>
                  <span className={s.laneName}>{l.id}</span>
                  {!owner ? (
                    <button
                      type="button"
                      className={s.smallBtn}
                      disabled={busy !== null}
                      onClick={() => act(l.id, "claim")}
                      aria-label={`Claim ${l.id}`}
                    >
                      {busy === l.id ? "Claiming…" : "Claim"}
                    </button>
                  ) : mine ? (
                    <button
                      type="button"
                      className={s.smallBtn}
                      disabled={busy !== null}
                      onClick={() => act(l.id, "release")}
                      aria-label={`Release ${l.id}`}
                    >
                      {busy === l.id ? "Releasing…" : "Release"}
                    </button>
                  ) : (
                    <Icon name="lane" size={18} {...(p ? { accent: seatVar(p.color) } : {})} />
                  )}
                </div>
                <span className={s.laneOwner}>
                  {p ? (
                    <>
                      <Avatar name={p.name || p.id} color={p.color} size={18} />
                      {p.name || p.id}
                      {mine ? " · you" : ""}
                    </>
                  ) : (
                    <>open{l.role === myRole ? " · fits your role" : ""}</>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
        {error ? (
          <p className={s.bad} role="alert">
            {error}
          </p>
        ) : null}
      </section>

      <div className={s.starts}>
        {Object.entries(capability?.intakeBindings ?? {}).map(([kind, binding]) => (
          <button
            key={kind}
            type="button"
            className={`${s.startBtn} ${s.floating}`}
            onClick={() => onStart(intakeId(caseId, kind))}
          >
            {binding.label ?? kind}
          </button>
        ))}
        <button
          type="button"
          className={`${s.startBtn} ${s.floating}`}
          onClick={() => onStart("words")}
        >
          Describe your request
        </button>
      </div>
    </div>
  );
}
