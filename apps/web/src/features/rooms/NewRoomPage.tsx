import { type FormEvent, type KeyboardEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useAuth } from "@/features/auth";
import {
  capabilityRoles,
  loadCapabilities,
  OPEN_STUDIOS,
  roleInfo,
  STUDIO_LANES,
  STUDIOS,
  type StudioId,
} from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Avatar, Button, Icon, Info, Mark, seatVar, TextField } from "@/shared/ui";
import { WorkspaceShell } from "@/shared/ui/Shell";
import { roomsApi, slugify } from "./api";
import s from "./newroom.module.css";
import { STUDIO_ICON } from "./studioIcons";

type Role = string;
type Errors = { [K in "title" | "slug" | "invite" | "form"]?: string | undefined };
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function NewRoomPage() {
  const { person } = useAuth();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [studio, setStudio] = useState<StudioId>(OPEN_STUDIOS[0] ?? "");
  const [chosenRole, setRole] = useState<Role>(person?.discipline ?? "");
  const [emails, setEmails] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  // Working alone: take every lane until colleagues join, who then get their role's lanes.
  // On by default while nobody is invited, unless the person chose.
  const [aloneChoice, setAloneChoice] = useState<boolean | null>(null);
  const alone = aloneChoice ?? (emails.length === 0 && !draft.trim());
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const [catalogReady, setCatalogReady] = useState(false);
  useEffect(() => {
    let active = true;
    loadCapabilities().then(
      () => {
        if (!active) return;
        setStudio((current) =>
          OPEN_STUDIOS.includes(current) ? current : (OPEN_STUDIOS[0] ?? ""),
        );
        setCatalogReady(true);
      },
      () => {
        if (active)
          setErrors((current) => ({
            ...current,
            form: "Available plugins could not be refreshed. Reload to try again.",
          }));
      },
    );
    return () => {
      active = false;
    };
  }, []);
  if (!person) return null;

  const address = slugTouched ? slug : slugify(title);
  const lanes = STUDIO_LANES[studio] ?? [];
  const roles = capabilityRoles(studio);
  const role = roles.some((r) => r.id === chosenRole) ? chosenRole : (roles[0]?.id ?? "");
  const disc = roleInfo(role);
  const me = seatVar(person.color);

  function addEmail(raw: string): boolean {
    const v = raw.trim().replace(/,$/, "").toLowerCase();
    if (!v) return true;
    if (!EMAIL.test(v)) {
      setErrors((e) => ({ ...e, invite: `${v} is not an email address.` }));
      return false;
    }
    if (v === person?.email) {
      setErrors((e) => ({ ...e, invite: "You already have a seat." }));
      return false;
    }
    if (emails.length >= 3) {
      setErrors((e) => ({ ...e, invite: "Four seats today: you and three colleagues." }));
      return false;
    }
    setEmails((l) => (l.includes(v) ? l : [...l, v]));
    setDraft("");
    setErrors((e) => ({ ...e, invite: undefined }));
    return true;
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === "," || e.key === " ") {
      if (draft.trim()) {
        e.preventDefault();
        addEmail(draft);
      } else if (e.key === "Enter") {
        e.preventDefault();
      }
    } else if (e.key === "Backspace" && !draft && emails.length) {
      setEmails((l) => l.slice(0, -1));
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const next: Errors = {};
    if (!catalogReady || !studio || !role) next.form = "Choose an installed capability and role.";
    if (!title.trim()) next.title = "Give the room a name.";
    if (!/^[a-z0-9-]{2,40}$/.test(address))
      next.slug = "Use 2 to 40 lowercase letters, digits and dashes.";
    if (draft.trim() && !addEmail(draft)) next.invite = errors.invite ?? "Check the email.";
    setErrors(next);
    if (Object.keys(next).length) return;
    const invite = draft.trim() ? [...emails, draft.trim().toLowerCase()] : emails;
    setBusy(true);
    try {
      const r = await roomsApi.create({
        id: address,
        title: title.trim(),
        studio,
        role,
        invite,
        alone,
      });
      try {
        sessionStorage.setItem(`consilith.roomlink.${r.id}`, r.link);
      } catch {
        /* the room page can make a new link */
      }
      navigate(`/rooms/${encodeURIComponent(r.id)}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setErrors({ slug: err.message });
      else
        setErrors({ form: err instanceof ApiError ? err.message : "The room could not be made." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <WorkspaceShell
      person={person}
      crumbs={
        <>
          <span aria-hidden>/</span>
          <Link to="/">Your rooms</Link>
          <span aria-hidden>/</span>
          <span aria-current="page">New room</span>
        </>
      }
    >
      <div className={s.page}>
        <form className={s.form} onSubmit={submit} noValidate>
          <h1 className={s.title}>Open a room</h1>
          <div className={s.two}>
            <TextField
              label="Room name"
              placeholder="What you are working on"
              value={title}
              onChange={(e) => {
                setTitle(e.target.value);
                setErrors((x) => ({ ...x, title: undefined }));
              }}
              error={errors.title ?? null}
            />
            <div className={s.field}>
              <label htmlFor="room-address" className={s.label}>
                Room address{" "}
                <Info text="Lowercase letters, digits and dashes. It becomes the room's link and does not change later." />
              </label>
              <div className={s.address} data-error={errors.slug ? true : undefined}>
                <span className={s.prefix}>/rooms/</span>
                <input
                  id="room-address"
                  value={address}
                  placeholder="made-from-the-name"
                  spellCheck={false}
                  autoCapitalize="none"
                  aria-invalid={errors.slug ? true : undefined}
                  aria-describedby="room-address-note"
                  onChange={(e) => {
                    setSlugTouched(true);
                    setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"));
                    setErrors((x) => ({ ...x, slug: undefined }));
                  }}
                />
              </div>
              {errors.slug ? (
                <span id="room-address-note" className={s.bad}>
                  {errors.slug}
                </span>
              ) : null}
            </div>
          </div>

          <fieldset className={s.group}>
            <legend className={s.label}>Studio to start in</legend>
            <div className={s.studios}>
              {(Object.keys(STUDIOS) as StudioId[]).map((id) => {
                const open = OPEN_STUDIOS.includes(id);
                return (
                  <label key={id} className={s.studio} data-disabled={open ? undefined : true}>
                    <input
                      type="radio"
                      name="studio"
                      className="visually-hidden"
                      checked={studio === id}
                      disabled={!open}
                      onChange={() => setStudio(id)}
                    />
                    <Icon name={STUDIO_ICON[id]?.icon ?? "solvers"} size={26} />
                    <span className={s.studioName}>
                      {STUDIOS[id]?.label ?? id}
                      {open ? null : <span className={s.later}>later</span>}
                    </span>
                    <span className={s.studioLanes}>{STUDIOS[id]?.lanes ?? ""}</span>
                  </label>
                );
              })}
            </div>
            {catalogReady && !OPEN_STUDIOS.length ? (
              <p className={s.note} role="status">
                No capabilities are installed. Ask your server operator to install a plugin, then
                reload this page to create a room.
              </p>
            ) : studio ? (
              <div className={s.accepts}>
                <Icon name={STUDIO_ICON[studio]?.start ?? "formula"} size={18} />
                <div>
                  <b>{STUDIOS[studio]?.label ?? studio} accepts</b>{" "}
                  {lowerFirst(STUDIOS[studio]?.grammar ?? "")}
                </div>
              </div>
            ) : null}
          </fieldset>

          <fieldset className={s.group}>
            <legend className={s.label}>Your role in this room</legend>
            <div className={s.roles}>
              {roles.map(({ id: r, label }) => (
                <label key={r} className={s.role}>
                  <input
                    type="radio"
                    name="role"
                    className="visually-hidden"
                    checked={role === r}
                    onChange={() => setRole(r)}
                  />
                  <Icon name="lane" size={22} accent={role === r ? me : "var(--ink-3)"} />
                  <span>
                    <span className={s.roleName}>{label}</span>
                    <span className={s.roleClaims}>
                      claims{" "}
                      {lanes
                        .filter((l) => l.role === r)
                        .map((l) => l.id)
                        .join(" · ")}
                    </span>
                  </span>
                </label>
              ))}
            </div>
            <label className={s.alone}>
              <input
                type="checkbox"
                checked={alone}
                onChange={(e) => setAloneChoice(e.target.checked)}
              />
              <span>Working alone for now: take every lane</span>
              <Info text="You own all lanes, so you can solve straight away. When a colleague joins, their role's lanes move to them, and the cross-check between roles comes back." />
            </label>
          </fieldset>

          <div className={s.group}>
            <label htmlFor="invite-email" className={s.label}>
              Invite colleagues
            </label>
            <div className={s.chipsField} data-error={errors.invite ? true : undefined}>
              <Icon name="invite" size={18} />
              {emails.map((m) => (
                <span key={m} className={s.chip}>
                  {m}
                  <button
                    type="button"
                    aria-label={`Remove ${m}`}
                    onClick={() => setEmails((l) => l.filter((x) => x !== m))}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden>
                      <path
                        d="M6 6l12 12M18 6L6 18"
                        stroke="currentColor"
                        strokeWidth="2.4"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                </span>
              ))}
              <input
                id="invite-email"
                type="email"
                inputMode="email"
                autoComplete="off"
                placeholder={emails.length ? "Add another" : "Add an email"}
                value={draft}
                aria-describedby="invite-note"
                onChange={(e) => {
                  setDraft(e.target.value);
                  setErrors((x) => ({ ...x, invite: undefined }));
                }}
                onKeyDown={onKey}
                onBlur={() => draft.trim() && addEmail(draft)}
              />
            </div>
            <span
              id="invite-note"
              className={errors.invite ? s.bad : s.hint}
              role={errors.invite ? "alert" : undefined}
            >
              {errors.invite ?? "Each colleague picks a role when they join. Up to four seats."}
            </span>
          </div>

          {errors.form ? (
            <p className={s.bad} role="alert">
              {errors.form}
            </p>
          ) : null}

          <div className={s.footer}>
            <Link to="/" className={s.cancel}>
              Cancel
            </Link>
            <Button type="submit" disabled={busy || !catalogReady || !studio || !role}>
              <Icon name="room" size={20} />
              {busy
                ? "Opening…"
                : emails.length || draft.trim()
                  ? "Create room and invite"
                  : "Create room"}
            </Button>
          </div>
        </form>

        <aside className={s.side}>
          <section className={s.panel} aria-labelledby="lanes-h">
            <div className={s.markRow}>
              <Mark
                size={56}
                seats={[me]}
                label="The room mark: one petal per seat; your seat is filled, three are open"
              />
              <div>
                <h2 id="lanes-h" className={s.panelTitle}>
                  {STUDIOS[studio]?.label ?? studio} lanes
                </h2>
                <p className={s.small}>One petal per seat; yours is filled.</p>
              </div>
            </div>
            <ul className={s.lanes}>
              {lanes.map((l) => {
                const mine = alone || l.role === role;
                return (
                  <li
                    key={l.id}
                    className={mine ? s.laneMine : s.laneOpen}
                    style={mine ? { borderColor: me } : undefined}
                  >
                    <span className={s.laneName}>{l.id}</span>
                    {mine ? (
                      <span className={s.laneOwner}>
                        <Avatar name={person.name} color={person.color} size={18} />
                        {person.name}
                      </span>
                    ) : (
                      <span className={s.small}>open</span>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className={s.note}>
              <Icon name="checking" size={15} />
              <span>The first solve opens when every lane has an owner.</span>
            </p>
          </section>

          <section className={s.panel} aria-labelledby="starts-h">
            <h2 id="starts-h" className={s.panelHead}>
              <Icon name="agent" size={18} accent={me} />
              Your agent starts with
            </h2>
            <ul className={s.starts}>
              <li>
                <Icon name="verified" size={16} />
                <span>Package-defined capabilities and checks</span>
              </li>
              <li>
                <Icon name="lane" size={16} />
                <span>{disc.goal || "Goals and checks come from the selected plugin."}</span>
              </li>
              <li>
                <Icon name="drawer" size={16} />
                <span>{disc.autonomy.replaceAll("_", " ")} · configurable in your agent</span>
              </li>
            </ul>
            <Link to="/settings/agent" className={s.link}>
              Change in your agent settings
            </Link>
          </section>
        </aside>
      </div>
    </WorkspaceShell>
  );
}

function lowerFirst(t: string): string {
  return t.charAt(0).toLowerCase() + t.slice(1);
}
