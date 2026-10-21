import { type CSSProperties, useEffect, useState } from "react";
import { AUTONOMY, type PluginDescriptor, roleInfo } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Button, Icon, seatVar } from "@/shared/ui";
import s from "./drawer.module.css";
import { Sheet } from "./Sheet";
import type { RoomConnection } from "./store";
import type { AgentConfig, Lesson, Person, PersonalAgent } from "./types";

const AUTONOMY_NOTE: Record<AgentConfig["autonomy"], string> = {
  observe: "Reads and comments. It may not write or propose.",
  suggest: "Every change it wants becomes a proposal to you.",
  edit_my_lanes: "Edits your lanes directly, proposes in everyone else's.",
  run_solvers: "Also asks the conductor to run solvers, within your tools and budget.",
};

const MODELS: { id: AgentConfig["modelProvider"]; label: string; soon?: boolean }[] = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "gemini", label: "Google Gemini" },
  { id: "scripted", label: "No model" },
  { id: "siwc", label: "ChatGPT plan" },
];

/** Your agent in this room: autonomy, goal, budget, watches, skills, tools, model, memory.
 * Saved as one whole AgentConfig (PUT replaces it), only by its owner. */
export function AgentDrawer({
  conn,
  person,
  agent,
  editable,
  plugins,
  onBring,
  onClose,
}: {
  conn: RoomConnection;
  person: Person;
  agent: PersonalAgent;
  editable: boolean;
  plugins: PluginDescriptor[];
  onBring: () => void;
  onClose: () => void;
}) {
  const [cfg, setCfg] = useState<AgentConfig>(agent.config);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [lessons, setLessons] = useState<Lesson[] | null>(null);
  const c = seatVar(person.color);
  const disc =
    plugins.flatMap((p) => p.roles).find((r) => r.id === person.role) ?? roleInfo(person.role);

  useEffect(() => {
    if (!dirty) setCfg(agent.config);
  }, [agent.config, dirty]);
  useEffect(() => {
    if (!editable) return;
    conn.memory().then(
      (m) => setLessons(m.personal.filter((l) => !l.deleted)),
      () => setLessons([]),
    );
  }, [conn, editable]);

  const change = (next: Partial<AgentConfig>) => {
    setCfg((x) => ({ ...x, ...next }));
    setDirty(true);
    setMsg(null);
  };
  const skills = Array.from(new Set(plugins.flatMap((p) => p.skills.map((skill) => skill.id))));
  const tools = Array.from(new Set(plugins.flatMap((p) => p.tools)));
  const allowed = (t: string) => cfg.toolsAllowed.includes(t) && !cfg.toolsDenied.includes(t);

  async function save() {
    setBusy(true);
    try {
      await conn.setConfig(agent.id, cfg);
      setDirty(false);
      setMsg({ ok: true, text: "Saved. The room log records the change." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Saving failed." });
    } finally {
      setBusy(false);
    }
  }

  const title = agent.brought ? (agent.card?.name ?? "Your A2A agent") : agent.id;
  return (
    <Sheet
      title={title}
      sub={
        agent.brought
          ? `Brought by ${person.name} over A2A · ${person.name}'s lanes and permissions`
          : `${editable ? "Your personal agent" : `${person.name}'s personal agent`} · Consilith agent · follows ${editable ? "you" : "them"} to every room`
      }
      icon={<Icon name={agent.brought ? "a2a" : "agent"} size={32} accent={c} />}
      onClose={onClose}
    >
      <div className={s.cols} style={{ "--c": c } as CSSProperties}>
        <div className={s.col}>
          <fieldset className={s.section} disabled={!editable}>
            <legend className={s.sectionHead}>
              <span className={s.h3}>Autonomy</span>
              <span className={s.small}>how far it goes without asking</span>
            </legend>
            <div className={s.options}>
              {AUTONOMY.map((a) => (
                <label key={a.id} className={s.option}>
                  <input
                    type="radio"
                    name="autonomy"
                    checked={cfg.autonomy === a.id}
                    onChange={() => change({ autonomy: a.id })}
                  />
                  <span className={s.optionName}>{a.label}</span>
                  <span className={s.optionNote}>{AUTONOMY_NOTE[a.id]}</span>
                </label>
              ))}
            </div>
            <p className={s.small}>Set by your role, {disc.label}.</p>
          </fieldset>

          <section className={s.section}>
            <h3 className={s.h3}>Goal</h3>
            <div className={s.goal}>
              <Icon name="decision" size={18} />
              <span>{cfg.goal || "none set"}</span>
              <span className={s.small}>what proposals optimise</span>
            </div>
          </section>

          <fieldset className={s.section} disabled={!editable}>
            <legend className={s.sectionHead}>
              <span className={s.h3}>Compute budget</span>
              <span className={s.small}>quick tier</span>
            </legend>
            <div className={s.budget}>
              <input
                type="range"
                min={5}
                max={300}
                step={5}
                value={cfg.computeBudgetS}
                aria-label="Seconds per quick run"
                onChange={(e) => change({ computeBudgetS: Number(e.target.value) })}
              />
              <span className={s.mono}>{cfg.computeBudgetS} s</span>
            </div>
            <p className={s.small}>
              Solver time per quick run. Larger runs go to the accurate queue.
            </p>
          </fieldset>

          <section className={s.section}>
            <div className={s.sectionHead}>
              <h3 className={s.h3}>Watches</h3>
              <span className={s.small}>checked on every result</span>
            </div>
            {cfg.watches.length ? (
              cfg.watches.map((w) => (
                <div key={w.id} className={s.watch}>
                  <Icon name="verified" size={18} />
                  <span>
                    {w.label}
                    <span className={s.small} style={{ display: "block" }}>
                      {w.case}
                    </span>
                  </span>
                  <span className={s.tag}>
                    {w.onBreak === "propose" ? "proposes when broken" : "warns when broken"}
                  </span>
                </div>
              ))
            ) : (
              <p className={s.small}>No watches yet.</p>
            )}
          </section>
        </div>

        <div className={s.col}>
          <fieldset className={s.section} disabled={!editable}>
            <legend className={s.sectionHead}>
              <span className={s.h3}>Skills</span>
              <span className={s.small}>
                {cfg.skills.length} of {skills.length} skill folders on
              </span>
            </legend>
            <div className={s.skills}>
              {skills.map((k) => {
                const on = cfg.skills.includes(k);
                return (
                  <button
                    key={k}
                    type="button"
                    className={s.skill}
                    aria-pressed={on}
                    onClick={() =>
                      change({
                        skills: on ? cfg.skills.filter((x) => x !== k) : [...cfg.skills, k],
                      })
                    }
                  >
                    {k}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <fieldset className={s.section} disabled={!editable}>
            <legend className={s.sectionHead}>
              <span className={s.h3}>Tools</span>
              <span className={s.small}>allow or deny each one</span>
            </legend>
            <div className={s.tools}>
              {tools.map((t) => (
                <label key={t} className={s.tool}>
                  <span>{t}</span>
                  <input
                    type="checkbox"
                    role="switch"
                    aria-checked={allowed(t)}
                    className={s.switch}
                    checked={allowed(t)}
                    onChange={(e) =>
                      change(
                        e.target.checked
                          ? {
                              toolsAllowed: [...new Set([...cfg.toolsAllowed, t])],
                              toolsDenied: cfg.toolsDenied.filter((x) => x !== t),
                            }
                          : {
                              toolsAllowed: cfg.toolsAllowed.filter((x) => x !== t),
                              toolsDenied: [...new Set([...cfg.toolsDenied, t])],
                            },
                      )
                    }
                  />
                </label>
              ))}
            </div>
            <p className={s.small}>
              Your agent asks the conductor to call these. It never calls a solver itself, and never
              in someone else's lane.
            </p>
          </fieldset>

          {!agent.brought ? (
            <fieldset className={s.section} disabled={!editable}>
              <legend className={s.sectionHead}>
                <span className={s.h3}>Model</span>
                <span className={s.small}>what your agent thinks with</span>
              </legend>
              <div className={s.models}>
                {MODELS.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className={s.model}
                    aria-pressed={cfg.modelProvider === m.id}
                    disabled={m.soon}
                    onClick={() => change({ modelProvider: m.id })}
                  >
                    {m.label}
                    {m.soon ? " · coming soon" : ""}
                  </button>
                ))}
              </div>
              <p className={s.small}>
                The key is set in your agent settings and held in server memory for your session
                only. It is never stored, logged or sent to the browser.
              </p>
            </fieldset>
          ) : null}

          {editable ? (
            <section className={s.section}>
              <div className={s.sectionHead}>
                <h3 className={s.h3}>Memory</h3>
                <span className={s.small}>lessons, across all your rooms</span>
              </div>
              {lessons?.length ? (
                <ul className={s.lessons}>
                  {lessons.map((l) => (
                    <li key={l.id}>{l.text}</li>
                  ))}
                </ul>
              ) : (
                <div className={s.memory}>
                  <Icon name="ledger" size={20} />
                  <div>
                    <div className={s.strong}>
                      {lessons === null ? "Loading…" : "No lessons yet"}
                    </div>
                    <p className={s.small}>
                      When you approve or decline a proposal, your reason becomes a lesson your
                      agent checks before it proposes again.
                    </p>
                  </div>
                </div>
              )}
            </section>
          ) : null}
        </div>
      </div>

      {editable ? (
        <div className={s.footer}>
          {msg ? (
            <span className={msg.ok ? s.small : s.bad} role="status">
              {msg.text}
            </span>
          ) : null}
          <Button variant="ghost" onClick={onBring}>
            <Icon name="a2a" size={18} />
            Bring your own agent
          </Button>
          <Button disabled={!dirty || busy} onClick={save}>
            {busy ? "Saving…" : "Save the drawer"}
          </Button>
        </div>
      ) : (
        <p className={s.small}>Only {person.name} can change this drawer.</p>
      )}
    </Sheet>
  );
}
