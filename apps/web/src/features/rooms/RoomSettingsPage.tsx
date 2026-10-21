import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useAuth } from "@/features/auth";
import { STUDIOS } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Avatar, Button, Icon } from "@/shared/ui";
import { WorkspaceShell } from "@/shared/ui/Shell";
import { type RoomSummary, roomsApi } from "./api";
import s from "./settings.module.css";

/** A room's settings, like a repository's: who is in it, and a danger zone. The creator can
 * delete the room after typing its address; everyone else can leave it. */
export function RoomSettingsPage() {
  const { id = "" } = useParams();
  const { person } = useAuth();
  const navigate = useNavigate();
  const [room, setRoom] = useState<RoomSummary | null | undefined>(undefined);
  const [typed, setTyped] = useState("");
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    roomsApi.list().then(
      (all) => setRoom(all.find((r) => r.id === id && !r.waiting) ?? null),
      () => setRoom(null),
    );
  }, [id]);

  if (!person) return null;
  const crumbs = (
    <>
      <span aria-hidden>/</span>
      <Link to="/">Your rooms</Link>
      <span aria-hidden>/</span>
      <span aria-current="page">{room?.title ?? id}</span>
    </>
  );
  if (room === undefined)
    return (
      <WorkspaceShell person={person} crumbs={crumbs}>
        {null}
      </WorkspaceShell>
    );
  if (room === null)
    return (
      <WorkspaceShell person={person} crumbs={crumbs}>
        <p className={s.lede}>
          You have no seat in this room. <Link to="/">Back to your rooms</Link>
        </p>
      </WorkspaceShell>
    );

  const owner = room.owner === person.id;

  async function act() {
    setBusy(true);
    setError(null);
    try {
      if (owner) await roomsApi.remove(id);
      else await roomsApi.leave(id);
      navigate("/", { replace: true });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That did not work.");
      setBusy(false);
    }
  }

  return (
    <WorkspaceShell person={person} crumbs={crumbs}>
      <div className={s.page}>
        <header className={s.head}>
          <h1 className={s.title}>Room settings</h1>
          <Link to={`/rooms/${encodeURIComponent(id)}`} className={s.open}>
            Open the room →
          </Link>
        </header>

        <section className={s.panel} aria-labelledby="general-h">
          <h2 id="general-h" className={s.h2}>
            General
          </h2>
          <dl className={s.dl}>
            <dt>Name</dt>
            <dd>{room.title}</dd>
            <dt>Address</dt>
            <dd className="mono">{room.id}</dd>
            <dt>Studio</dt>
            <dd>{STUDIOS[room.studio]?.label ?? room.studio}</dd>
            <dt>Your role</dt>
            <dd>{room.role ?? "invited"}</dd>
          </dl>
        </section>

        <section className={s.panel} aria-labelledby="people-h">
          <h2 id="people-h" className={s.h2}>
            People · {room.seats.length} of 4 seats
          </h2>
          <ul className={s.people}>
            {room.seats.map((p) => (
              <li key={p.id}>
                <Avatar name={p.name || p.id} color={p.color} size={28} />
                <span>{p.name || p.id}</span>
                {p.id === room.owner ? <span className={s.tag}>creator</span> : null}
                {p.id === person.id ? <span className={s.tag}>you</span> : null}
              </li>
            ))}
          </ul>
        </section>

        <section className={s.danger} aria-labelledby="danger-h">
          <h2 id="danger-h" className={s.h2}>
            Danger zone
          </h2>
          <div className={s.dangerRow}>
            <div>
              <div className={s.strong}>{owner ? "Delete this room" : "Leave this room"}</div>
              <p className={s.small}>
                {owner
                  ? "Its whole history, journal, seats and invites are deleted for everyone. This cannot be undone."
                  : "Your lanes open again and your agent leaves with you. You need a new invite to come back."}
              </p>
            </div>
            {!sure ? (
              <button type="button" className={s.dangerBtn} onClick={() => setSure(true)}>
                {owner ? "Delete this room" : "Leave this room"}
              </button>
            ) : null}
          </div>
          {sure ? (
            <div className={s.confirm}>
              {owner ? (
                <label className={s.confirmLabel}>
                  <span>
                    Type <b className="mono">{room.id}</b> to confirm
                  </span>
                  <input
                    className={s.input}
                    value={typed}
                    autoComplete="off"
                    spellCheck={false}
                    onChange={(e) => setTyped(e.target.value)}
                  />
                </label>
              ) : (
                <p className={s.small}>Leave {room.title}?</p>
              )}
              <div className={s.confirmBtns}>
                <Button variant="ghost" onClick={() => setSure(false)} disabled={busy}>
                  Cancel
                </Button>
                <button
                  type="button"
                  className={s.dangerSolid}
                  disabled={busy || (owner && typed !== room.id)}
                  onClick={act}
                >
                  <Icon name="warning" size={16} />
                  {busy
                    ? owner
                      ? "Deleting…"
                      : "Leaving…"
                    : owner
                      ? "I understand, delete this room"
                      : "Leave this room"}
                </button>
              </div>
              {error ? (
                <p className={s.bad} role="alert">
                  {error}
                </p>
              ) : null}
            </div>
          ) : null}
        </section>
      </div>
    </WorkspaceShell>
  );
}
