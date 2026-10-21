import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { agentApi, type MyAgent, PROVIDERS } from "@/features/agent/api";
import { useAuth } from "@/features/auth";
import { AUTONOMY, PLUGINS, STUDIOS, type StudioId } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Avatar, ButtonLink, Icon, type IconName, seatVar } from "@/shared/ui";
import { WorkspaceShell } from "@/shared/ui/Shell";
import { inviteToken, type RoomSummary, roomsApi } from "./api";
import s from "./rooms.module.css";
import { STUDIO_ICON } from "./studioIcons";

const ROOM_AGENTS: { icon: IconName; name: string; note: string }[] = [
  { icon: "conductor", name: "Conductor", note: "routes, composes" },
  { icon: "swarm", name: "Swarm", note: "explores designs" },
  { icon: "solvers", name: "Solvers", note: "tools from active plugins" },
  { icon: "referee", name: "Referee", note: "checks, signs" },
];

export function HomePage() {
  const { person } = useAuth();
  const [rooms, setRooms] = useState<RoomSummary[] | null>(null);
  const [agent, setAgent] = useState<MyAgent | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    roomsApi.list().then(setRooms, (e) => {
      setRooms([]);
      setFailed(e instanceof ApiError ? e.message : "Your rooms could not be loaded.");
    });
    agentApi.get().then(setAgent, () => setAgent(null));
  }, []);

  if (!person) return null;

  return (
    <WorkspaceShell person={person}>
      <div className={s.home}>
        <div className={s.main}>
          <div className={s.head}>
            <div>
              <h1 className={s.title}>Your rooms</h1>
              <p className={s.lede}>
                A room is one shared model you build with colleagues and everyone's agents.
              </p>
            </div>
            <ButtonLink to="/rooms/new">
              <Icon name="room" size={20} />
              New room
            </ButtonLink>
          </div>

          {failed ? (
            <p className={s.bad} role="alert">
              {failed}
            </p>
          ) : null}

          {rooms === null ? (
            <div className={s.skeleton} role="status" aria-label="Loading your rooms" />
          ) : rooms.length ? (
            <section aria-label="Rooms" className={s.roomList}>
              {rooms.map((r) => (
                <RoomCard key={r.id} room={r} me={person.id} />
              ))}
              <JoinBox compact />
            </section>
          ) : (
            <section className={s.empty}>
              <div className={s.emptyArt}>
                <Icon name="room" size={56} />
              </div>
              <div className={s.emptyBody}>
                <div>
                  <h2 className={s.h2}>No rooms yet</h2>
                  <p className={s.p}>
                    Open a room and invite colleagues, or join one from an invite link. Your rooms
                    appear here with who is inside and what waits on you.
                  </p>
                </div>
                <JoinBox />
              </div>
            </section>
          )}

          <section className={s.studios} aria-labelledby="studios-h">
            <h2 id="studios-h" className={s.caps}>
              Studios a room can open
            </h2>
            <div className={s.studioGrid}>
              {(Object.keys(STUDIOS) as StudioId[]).map((id) => (
                <div key={id} className={s.studio}>
                  <Icon name={STUDIO_ICON[id]?.icon ?? "solvers"} size={30} />
                  <div className={s.studioName}>{STUDIOS[id]?.label ?? id}</div>
                  <div className={s.studioText}>{STUDIOS[id]?.blurb ?? ""}</div>
                  <div className={s.studioStart}>
                    <Icon name={STUDIO_ICON[id]?.start ?? "formula"} size={14} />
                    {STUDIOS[id]?.starts ?? ""}
                  </div>
                </div>
              ))}
            </div>
            <p className={s.note}>
              {Object.keys(STUDIOS).length
                ? "Capabilities and roles come from installed plugins."
                : "No capabilities are installed. Ask your server operator to install a plugin, then reload this page."}
            </p>
          </section>
        </div>

        <aside className={s.side}>
          <section className={s.panel} aria-labelledby="agent-h">
            <div className={s.panelHead}>
              <h2 id="agent-h" className={s.caps}>
                Your agent
              </h2>
              <Link to="/settings/agent" className={s.panelLink}>
                <Icon name="drawer" size={16} />
                Change
              </Link>
            </div>
            <div className={s.agentRow}>
              <Icon
                name={agent?.kind === "brought" ? "a2a" : "agent"}
                size={40}
                accent={seatVar(person.color)}
              />
              <div>
                <div className={s.agentName}>{person.id}.agent</div>
                <div className={s.p}>{agentLine(agent)}</div>
              </div>
            </div>
            <div className={s.facts}>
              <Icon name="lane" size={18} />
              <span className={s.factKey}>Lanes</span>
              <span>yours only</span>
              <Icon name="verified" size={18} />
              <span className={s.factKey}>Watches</span>
              <span>none yet</span>
              <Icon name="ledger" size={18} />
              <span className={s.factKey}>Lessons</span>
              <span>none yet</span>
            </div>
            <p className={s.note}>Lessons follow you to every room.</p>
          </section>

          <section className={s.panel} aria-labelledby="room-agents-h">
            <h2 id="room-agents-h" className={s.caps}>
              Room agents · owned by nobody
            </h2>
            <ul className={s.roomAgents}>
              {ROOM_AGENTS.map((a) => (
                <li key={a.name}>
                  <Icon name={a.icon} size={20} />
                  <span className={s.raName}>{a.name}</span>
                  <span className={s.raNote}>{a.note}</span>
                </li>
              ))}
            </ul>
            <div className={s.notInstalled}>
              <Icon name="warning" size={16} />
              <span>
                {Object.values(PLUGINS).length} installed plugins. Activate capabilities in a room
                to use their tools, skills and checks.
              </span>
            </div>
          </section>
        </aside>
      </div>
    </WorkspaceShell>
  );
}

function agentLine(a: MyAgent | null): string {
  if (!a) return "Not set up yet";
  const autonomy = AUTONOMY.find((x) => x.id === a.autonomy)?.label ?? "";
  if (a.kind === "brought") return `Your A2A agent · ${autonomy}`;
  const provider = PROVIDERS.find((p) => p.id === a.provider)?.label ?? a.provider;
  return `Consilith agent · ${provider} · ${autonomy}`;
}

function RoomCard({ room, me }: { room: RoomSummary; me: string }) {
  const others = room.seats.filter((x) => x.id !== me);
  return (
    <div className={s.roomWrap}>
      <Link to={`/rooms/${encodeURIComponent(room.id)}`} className={s.roomCard}>
        <Icon name={STUDIO_ICON[room.studio]?.icon ?? "room"} size={32} />
        <div className={s.roomBody}>
          <div className={s.roomTitle}>{room.title}</div>
          <div className={s.roomMeta}>
            <span>{STUDIOS[room.studio]?.label ?? room.studio}</span>
            {room.role ? <span>· you are {room.role}</span> : null}
            <span className="mono">· {room.id}</span>
          </div>
        </div>
        <div className={s.seats}>
          {room.seats.map((x) => (
            <Avatar key={x.id} name={x.name || x.id} color={x.color} size={28} />
          ))}
          <span className="visually-hidden">{room.seats.length} of 4 seats taken</span>
        </div>
        <div className={s.waiting}>
          {room.waiting ? (
            <span className={s.waitPill}>invited · open the email link</span>
          ) : (
            <span className={s.note}>
              {others.length ? `with ${others.map((o) => o.name || o.id).join(", ")}` : "only you"}
            </span>
          )}
        </div>
      </Link>
      {room.waiting ? null : (
        <Link
          to={`/rooms/${encodeURIComponent(room.id)}/settings`}
          className={s.roomGear}
          aria-label={`Settings for ${room.title}`}
          title="Room settings"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden>
            <path
              d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinejoin="round"
            />
          </svg>
        </Link>
      )}
    </div>
  );
}

function JoinBox({ compact = false }: { compact?: boolean }) {
  const navigate = useNavigate();
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);
  function submit(e: FormEvent) {
    e.preventDefault();
    const t = inviteToken(link);
    if (!t) {
      setError("Paste the whole invite link, like …/invite/3f9a…");
      return;
    }
    navigate(`/invite/${t}`);
  }
  return (
    <form className={compact ? s.joinCompact : s.join} onSubmit={submit} noValidate>
      <label htmlFor="invite-link" className="visually-hidden">
        Invite link
      </label>
      <div className={s.joinField} data-error={error ? true : undefined}>
        <Icon name="invite" size={18} />
        <input
          id="invite-link"
          type="url"
          inputMode="url"
          placeholder="Paste an invite link"
          value={link}
          onChange={(e) => {
            setLink(e.target.value);
            setError(null);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "invite-error" : undefined}
        />
      </div>
      <button type="submit" className={s.joinBtn}>
        Join
      </button>
      {error ? (
        <span id="invite-error" className={s.bad} role="alert">
          {error}
        </span>
      ) : null}
    </form>
  );
}
