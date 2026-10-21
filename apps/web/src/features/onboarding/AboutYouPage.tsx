import { type FormEvent, Fragment, useState } from "react";
import { useNavigate } from "react-router";
import { authApi, useAuth } from "@/features/auth";
import { DISCIPLINES, type DisciplineId, roleInfo } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Avatar, Button, Icon, SEAT_COLORS, seatVar, TextField } from "@/shared/ui";
import { OnboardingShell } from "@/shared/ui/Shell";
import s from "./onboarding.module.css";

export function AboutYouPage() {
  const { person, setPerson, signOut } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState(person?.name ?? "");
  const [color, setColor] = useState(person?.color ?? "blue");
  const [disc, setDisc] = useState<DisciplineId>(
    person?.discipline || Object.keys(DISCIPLINES)[0] || "",
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!person) return null;
  const agentId = `${person.id}.agent`;
  const shown = name.trim() || "Your name";

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setError("Tell the room your name.");
      return;
    }
    setBusy(true);
    try {
      const r = await authApi.saveProfile({ name: name.trim(), color, discipline: disc });
      setPerson(r.person);
      navigate("/welcome/agent");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Saving failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <OnboardingShell step={1} of={2} label="About you">
      <div>
        <h1 className={s.title}>How the room should know you</h1>
        <p className={s.lede}>Your profile in every room; your role is chosen per room.</p>
      </div>
      <form className={s.cols} onSubmit={submit} noValidate>
        <div className={s.form}>
          <TextField
            label="Your name"
            autoComplete="name"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            error={error}
          />
          <fieldset className={s.fieldset}>
            <legend className={s.legend}>Your colour</legend>
            <div className={s.swatches}>
              {SEAT_COLORS.map((c) => (
                <label key={c} className={s.swatch} style={{ background: seatVar(c) }}>
                  <input
                    type="radio"
                    name="color"
                    value={c}
                    checked={color === c}
                    onChange={() => setColor(c)}
                    className="visually-hidden"
                  />
                  <span className="visually-hidden">{c}</span>
                </label>
              ))}
            </div>
            <span className={s.hint}>
              Your agent wears it too. If it is taken in a room, you get the next free one.
            </span>
          </fieldset>
          <fieldset className={s.fieldset}>
            <legend className={s.legend}>Your home discipline</legend>
            <span className={s.hint}>
              A preference for roles supplied by installed plugins. Choose your role in each room.
            </span>
            <div className={s.choices}>
              {(Object.keys(DISCIPLINES) as DisciplineId[]).map((d) => (
                <label key={d} className={s.choice}>
                  <input
                    type="radio"
                    name="disc"
                    value={d}
                    checked={disc === d}
                    onChange={() => setDisc(d)}
                  />
                  <span className={s.choiceBody}>
                    <span className={s.choiceTitle}>{roleInfo(d).label}</span>
                    <span className={s.skillLine}>
                      {roleInfo(d).skills.length
                        ? roleInfo(d).skills.map((k, i) => (
                            <Fragment key={k}>
                              {i ? " · " : ""}
                              <span className={s.nowrap}>{k}</span>
                            </Fragment>
                          ))
                        : "Start with no skills and pick your own"}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>

        <aside className={s.preview} aria-label="How others see you">
          <span className={s.caps}>How others see you</span>
          <div className={s.previewBox}>
            <span className={s.small}>In the presence bar</span>
            <span className={s.pill}>
              <Avatar name={shown} color={color} size={28} />
              <Icon name="agent" size={20} accent={seatVar(color)} />
              <span>{shown}</span>
            </span>
            <span className={s.small}>On the shared canvas</span>
            <span className={s.cursor}>
              <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
                <path d="M4 3l16 7-7 2-3 7z" style={{ fill: seatVar(color) }} />
              </svg>
              <span className={s.cursorTag} style={{ background: seatVar(color) }}>
                {shown}
              </span>
            </span>
            <div className={s.agentCard}>
              <span className={s.small}>Your agent</span>
              <div className={s.agentHead}>
                <Icon name="agent" size={32} accent={seatVar(color)} />
                <div>
                  <div className={s.agentName}>{agentId}</div>
                  <div className={s.small}>{roleInfo(disc).label}</div>
                </div>
              </div>
              <div className={s.chips}>
                {roleInfo(disc).skills.map((k) => (
                  <span key={k} className={s.chip}>
                    {k}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </aside>

        <div className={s.footer}>
          <Button variant="ghost" onClick={() => void signOut()}>
            Sign out
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Continue to your agent"} <span aria-hidden>→</span>
          </Button>
        </div>
      </form>
    </OnboardingShell>
  );
}
