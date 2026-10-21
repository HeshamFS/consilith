import { type CSSProperties, type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { agentApi, cardUrlProblem } from "@/features/agent/api";
import { useAuth } from "@/features/auth";
import { roleInfo } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Avatar, Button, Icon, Mark, seatVar, TextField } from "@/shared/ui";
import { InviteShell } from "@/shared/ui/Shell";
import { type InviteView, roomsApi } from "./api";
import s from "./join.module.css";

type Role = string;

export function JoinPage() {
  const { token = "" } = useParams();
  const { person } = useAuth();
  const navigate = useNavigate();
  const [inv, setInv] = useState<InviteView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [kind, setKind] = useState<"consilith" | "brought">("consilith");
  const [cardUrl, setCardUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    roomsApi
      .invitation(token)
      .then(setInv, (e) =>
        setLoadError(e instanceof ApiError ? e.message : "This invite could not be opened."),
      );
    agentApi.get().then(
      (a) => {
        setKind(a.kind);
        setCardUrl(a.cardUrl ?? "");
      },
      () => undefined,
    );
  }, [token]);

  if (!person) return null;
  const me = seatVar(person.color);

  if (loadError) {
    return (
      <InviteShell person={person}>
        <div className={s.dead}>
          <Mark size={56} label="Consilith" />
          <h1 className={s.title}>This invite does not open a room.</h1>
          <p className={s.lede}>{loadError} Ask whoever invited you for a new link.</p>
          <Link to="/">Go to your rooms</Link>
        </div>
      </InviteShell>
    );
  }
  if (!inv) {
    return (
      <InviteShell person={person}>
        <div className={s.dead} role="status">
          <Mark size={56} loading label="Opening the invite" />
        </div>
      </InviteShell>
    );
  }

  const capability = inv.room.capabilities?.find((c) => c.id === inv.room.studio);
  const lanes = (capability?.lanes ?? []).map((l) => ({
    role: l.defaultRole ?? l.default_role ?? l.role,
    ...l,
    owner: inv.lanes?.[inv.room.studio]?.[l.id] ?? null,
  }));
  const ownerName = (id: string | null) => inv.seats.find((x) => x.id === id)?.name || id || "";
  // Yours on joining: open lanes of your role, and those a colleague of the other role held alone.
  const seatRole = (id: string | null) => inv.seats.find((x) => x.id === id)?.role;
  const openFor = (r: Role) =>
    lanes.filter((l) => l.role === r && (!l.owner || seatRole(l.owner) !== r));
  const already = inv.seated || inv.seats.some((x) => x.id === person.id);
  const roles = capability?.roles ?? [];
  const preferred =
    roles.find((r) => r.id === person.discipline && openFor(r.id).length)?.id ??
    roles.find((r) => openFor(r.id).length)?.id ??
    roles[0]?.id ??
    "";
  const chosen = role && roles.some((r) => r.id === role) ? role : preferred;
  const claims = openFor(chosen).map((l) => l.id);
  const disc = { ...roleInfo(chosen), ...roles.find((r) => r.id === chosen) };
  const open = inv.full ? 0 : 4 - inv.seats.length;
  const urlProblem = kind === "brought" ? cardUrlProblem(cardUrl) : null;

  async function join(e: FormEvent) {
    e.preventDefault();
    if (urlProblem) {
      setError(urlProblem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await roomsApi.accept(token, {
        role: chosen,
        agent: kind === "brought" ? { kind, cardUrl: cardUrl.trim() } : { kind },
      });
      // The room hands this role's lanes over on accept, including any a colleague held alone.
      navigate(`/rooms/${encodeURIComponent(r.room)}`, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Joining failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <InviteShell person={person}>
      <form className={s.card} onSubmit={join} noValidate>
        <div className={s.top}>
          <span className={s.caps}>
            <Icon name="invite" size={16} accent={me} />
            Invitation · {capability?.label ?? inv.room.studio} room
          </span>
          <span className={`${s.caps} ${s.addr}`}>
            {location.host}/rooms/{inv.room.id}
          </span>
        </div>
        <div>
          <h1 className={s.title}>
            {inv.inviter.name || inv.inviter.id || "A colleague"} invited you to{" "}
            <i>{inv.room.title}</i>
          </h1>
          <p className={s.lede}>
            {already
              ? "You already have a seat in this room."
              : "Pick the role you play in this room and who joins with you. Nothing changes in the room until you join."}
          </p>
        </div>

        <div className={s.cols}>
          <div className={s.form}>
            <fieldset className={s.group}>
              <legend className={s.label}>Your role in this room</legend>
              <div className={s.roles}>
                {roles.map(({ id: r }) => {
                  const free = openFor(r);
                  const taken = lanes.filter((l) => l.role === r && l.owner);
                  return (
                    <label key={r} className={s.role} style={{ "--me": me } as CSSProperties}>
                      <input
                        type="radio"
                        name="role"
                        className="visually-hidden"
                        checked={chosen === r}
                        onChange={() => setRole(r)}
                      />
                      <Icon name="lane" size={22} accent={chosen === r ? me : "var(--ink-3)"} />
                      <span className={s.roleBody}>
                        <span className={s.roleName}>
                          {roles.find((role) => role.id === r)?.label ?? r}
                        </span>
                        {free.length ? (
                          <span className={s.claims}>
                            claims {free.map((l) => l.id).join(" · ")}
                            {taken.length ? "" : free.length > 1 ? ", all open" : ", open"}
                          </span>
                        ) : (
                          <span className={s.small}>
                            {taken.map((l) => l.label).join(" and ")}{" "}
                            {taken.length > 1 ? "are" : "is"} {ownerName(taken[0]?.owner ?? null)}
                            's; you could still join and propose there.
                          </span>
                        )}
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            <fieldset className={s.group}>
              <legend className={s.label}>Who joins with you</legend>
              <div className={s.agents}>
                <label className={s.agent} style={{ "--me": me } as CSSProperties}>
                  <input
                    type="radio"
                    name="agent"
                    className="visually-hidden"
                    checked={kind === "consilith"}
                    onChange={() => setKind("consilith")}
                  />
                  <Icon name="agent" size={26} accent={me} />
                  <span className={s.roleName}>{person.id}.agent</span>
                  <span className={s.small}>Your Consilith agent, spawned on join</span>
                </label>
                <label className={s.agent} style={{ "--me": me } as CSSProperties}>
                  <input
                    type="radio"
                    name="agent"
                    className="visually-hidden"
                    checked={kind === "brought"}
                    onChange={() => setKind("brought")}
                  />
                  <Icon name="a2a" size={26} />
                  <span className={s.roleName}>Your own A2A agent</span>
                  <span className={s.small}>From its Agent Card, with your permissions</span>
                </label>
              </div>
              {kind === "brought" ? (
                <TextField
                  label="Its Agent Card URL"
                  type="url"
                  placeholder="https://…/.well-known/agent-card.json"
                  value={cardUrl}
                  onChange={(e) => setCardUrl(e.target.value)}
                  error={cardUrl && urlProblem ? urlProblem : null}
                  hint="The room fetches the card once, over https, and shows everyone what it may do."
                />
              ) : null}
            </fieldset>

            <div className={s.starts}>
              <div className={s.label}>
                {kind === "brought" ? "Your A2A agent" : `${person.id}.agent`} starts with
              </div>
              <ul>
                <li>
                  <Icon name="verified" size={16} />
                  <span>Checks and watches supplied by the active plugin</span>
                </li>
                <li>
                  <Icon name="lane" size={16} />
                  <span>{disc.goal || "Set your goal in the agent drawer"}</span>
                </li>
                <li>
                  <Icon name="drawer" size={16} />
                  <span>{disc.autonomy.replaceAll("_", " ")} · configurable in your agent</span>
                </li>
              </ul>
              {kind === "consilith" ? (
                <div className={s.chips}>
                  {disc.skills.map((k) => (
                    <span key={k} className={s.chip} style={{ "--me": me } as CSSProperties}>
                      {k}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          </div>

          <aside className={s.side}>
            <span className={s.caps}>The room you are joining</span>
            <section className={s.roomBox}>
              <div className={s.markRow}>
                <Mark
                  size={64}
                  seats={[...inv.seats.map((x) => seatVar(x.color)), already ? null : me].filter(
                    (c): c is string => Boolean(c),
                  )}
                  label={`The room mark: ${inv.seats.length} seat${inv.seats.length === 1 ? "" : "s"} filled, yours joining`}
                />
                <p className={s.small}>
                  {inv.seats.map((x) => x.name || x.id).join(" and ")}
                  {inv.seats.length === 1 ? "'s seat is" : "'s seats are"} filled; yours joins in
                  your colour. {Math.max(open - 1, 0)} seat{open - 1 === 1 ? " is" : "s are"} open.
                </p>
              </div>
              <ul className={s.people}>
                {inv.seats.map((x) => (
                  <li key={x.id}>
                    <div className={s.personRow}>
                      <Avatar name={x.name || x.id} color={x.color} size={26} />
                      <span className={s.personName}>{x.name || x.id}</span>
                    </div>
                    <div className={s.agentRow}>
                      <Icon name="agent" size={20} accent={seatVar(x.color)} />
                      <span>{x.id}.agent</span>
                    </div>
                  </li>
                ))}
              </ul>
              <ul className={s.lanes}>
                {lanes.map((l) => {
                  const owner = inv.seats.find((x) => x.id === l.owner);
                  const mine = !l.owner && l.role === chosen;
                  return (
                    <li
                      key={l.id}
                      className={owner ? s.laneTaken : mine ? s.laneMine : s.laneOpen}
                      style={
                        {
                          "--c": owner ? seatVar(owner.color) : me,
                        } as CSSProperties
                      }
                    >
                      <span className={s.laneName}>{l.id}</span>
                      <span className={s.laneWho}>
                        {owner ? owner.name || owner.id : mine ? "yours when you join" : "open"}
                      </span>
                    </li>
                  );
                })}
              </ul>
              <div className={s.roomAgents}>
                <Icon name="conductor" size={18} />
                <Icon name="swarm" size={18} />
                <Icon name="solvers" size={18} />
                <Icon name="referee" size={18} />
                <span className={s.small}>Room agents, owned by nobody</span>
              </div>
            </section>
            <p className={s.note}>
              <Icon name="checking" size={15} />
              <span>
                {lanes.every((l) => l.owner || l.role === chosen)
                  ? "When you join, every lane has an owner and the room can run its first solve."
                  : "The first solve opens when every lane has an owner."}
              </span>
            </p>
          </aside>
        </div>

        {error ? (
          <p className={s.bad} role="alert">
            {error}
          </p>
        ) : null}

        <div className={s.footer}>
          <Link to="/" className={s.cancel}>
            Not now
          </Link>
          {already ? (
            <Button onClick={() => navigate(`/rooms/${encodeURIComponent(inv.room.id)}`)}>
              <Icon name="room" size={20} />
              Enter the room
            </Button>
          ) : (
            <Button type="submit" disabled={busy || open <= 0}>
              <Icon name="room" size={20} />
              {open <= 0
                ? "All four seats are taken"
                : busy
                  ? "Joining…"
                  : claims.length
                    ? `Join and claim ${claims.join(", ")}`
                    : "Join and propose"}
            </Button>
          )}
        </div>
      </form>
    </InviteShell>
  );
}
