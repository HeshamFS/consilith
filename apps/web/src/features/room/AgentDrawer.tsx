import { type CSSProperties, useEffect, useState } from "react";
import {
  agentApi,
  loadProviders,
  type ModelOption,
  PROVIDERS,
  type ProviderInfo,
} from "@/features/agent/api";
import { ModelPicker } from "@/features/agent/ModelPicker";
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
  const [providers, setProviders] = useState<ProviderInfo[]>(() => [...PROVIDERS]);
  const [models, setModels] = useState<ModelOption[] | null>(null);
  const [modelsNote, setModelsNote] = useState<string | null>(null);
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

  // One provider list, from the server's registry.
  useEffect(() => {
    loadProviders().then(
      (list) => setProviders([...list]),
      () => undefined,
    );
  }, []);

  // The models the chosen provider's connection can use. Nothing is loaded for a provider that
  // takes no model, or for a drawer the person cannot edit.
  const provider = providers.find((p) => p.id === cfg.modelProvider);
  const needsModel = Boolean(provider?.needsModel);
  useEffect(() => {
    setModels(null);
    if (!editable || agent.brought || !needsModel) {
      setModelsNote(null);
      return;
    }
    let live = true;
    setModelsNote("Loading models…");
    agentApi.models(cfg.modelProvider).then(
      (r) => {
        if (!live) return;
        setModels(r.models);
        setModelsNote(r.models.length ? null : "This connection lists no chat models.");
      },
      (e: unknown) => {
        if (live)
          setModelsNote(e instanceof ApiError ? e.message : "The models could not be loaded.");
      },
    );
    return () => {
      live = false;
    };
  }, [cfg.modelProvider, needsModel, editable, agent.brought]);

  const change = (next: Partial<AgentConfig>) => {
    setCfg((x) => ({ ...x, ...next }));
    setDirty(true);
    setMsg(null);
  };
  // Changing the provider clears the model: an id belongs to one maker.
  const chooseProvider = (id: string) => {
    if (id === cfg.modelProvider) return;
    setCfg((x) => {
      const { modelName: _cleared, ...rest } = x;
      return { ...rest, modelProvider: id };
    });
    setDirty(true);
    setMsg(null);
  };
  const skills = Array.from(new Set(plugins.flatMap((p) => p.skills.map((skill) => skill.id))));
  const tools = Array.from(new Set(plugins.flatMap((p) => p.tools)));
  const allowed = (t: string) => cfg.toolsAllowed.includes(t) && !cfg.toolsDenied.includes(t);

  async function save() {
    if (needsModel && !cfg.modelName?.trim()) {
      setMsg({ ok: false, text: "Choose a model for this provider first." });
      return;
    }
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
  const toggleTool = (t: string, on: boolean) =>
    change(
      on
        ? {
            toolsAllowed: [...new Set([...cfg.toolsAllowed, t])],
            toolsDenied: cfg.toolsDenied.filter((x) => x !== t),
          }
        : {
            toolsAllowed: cfg.toolsAllowed.filter((x) => x !== t),
            toolsDenied: [...new Set([...cfg.toolsDenied, t])],
          },
    );
  const owner = editable ? "Your" : `${person.name}'s`;
  return (
    <Sheet
      title={title}
      sub={
        agent.brought
          ? `Brought by ${person.name} over A2A`
          : `${owner} Consilith agent · follows ${editable ? "you" : "them"} to every room`
      }
      icon={<Icon name={agent.brought ? "a2a" : "agent"} size={20} accent={c} />}
      narrow
      onClose={onClose}
    >
      <div className={s.drawer} style={{ "--c": c } as CSSProperties}>
        {!agent.brought ? (
          <fieldset className={s.block} disabled={!editable}>
            <legend className={s.eyebrow}>
              Model <span>what it thinks with</span>
            </legend>
            <fieldset className={s.segments}>
              <legend className="visually-hidden">Provider</legend>
              {providers.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={s.segment}
                  aria-pressed={cfg.modelProvider === p.id}
                  onClick={() => chooseProvider(p.id)}
                >
                  {p.label}
                </button>
              ))}
            </fieldset>
            {needsModel ? (
              <ModelPicker
                models={models}
                value={cfg.modelName ?? ""}
                onChange={(m) => change({ modelName: m })}
                note={modelsNote}
              />
            ) : null}
            {provider ? <p className={s.hint}>{provider.dataTerms}</p> : null}
            <p className={s.hint}>Your key stays in server memory for your session only.</p>
          </fieldset>
        ) : null}

        <fieldset className={s.block} disabled={!editable}>
          <legend className={s.eyebrow}>
            Autonomy <span>how far it goes without asking</span>
          </legend>
          <div className={s.steps}>
            {AUTONOMY.map((a) => (
              <label key={a.id} className={s.step}>
                <input
                  type="radio"
                  name="autonomy"
                  checked={cfg.autonomy === a.id}
                  onChange={() => change({ autonomy: a.id })}
                />
                <span>{a.label}</span>
              </label>
            ))}
          </div>
          <p className={s.hint}>
            {AUTONOMY_NOTE[cfg.autonomy]} Set by your role, {disc.label}.
          </p>
        </fieldset>

        <fieldset className={s.block} disabled={!editable}>
          <legend className={s.eyebrow}>Goal</legend>
          <textarea
            className={s.goalInput}
            aria-label="Goal"
            rows={3}
            value={cfg.goal}
            placeholder="What should its proposals optimise?"
            onChange={(e) => change({ goal: e.target.value })}
          />
          <p className={s.hint}>What its proposals optimise.</p>
        </fieldset>

        <fieldset className={s.block} disabled={!editable}>
          <legend className={s.eyebrow}>
            Tools{" "}
            <span>
              {tools.filter(allowed).length} of {tools.length} on
            </span>
          </legend>
          {tools.length ? (
            <ul className={s.toolList}>
              {tools.map((t) => (
                <li key={t}>
                  <label className={s.toolRow} title={t}>
                    <span className={s.toolName}>{t}</span>
                    <input
                      type="checkbox"
                      role="switch"
                      aria-checked={allowed(t)}
                      aria-label={t}
                      className={s.switch}
                      checked={allowed(t)}
                      onChange={(e) => toggleTool(t, e.target.checked)}
                    />
                  </label>
                </li>
              ))}
            </ul>
          ) : (
            <p className={s.hint}>No plugin tools in this room.</p>
          )}
          <p className={s.hint}>
            It asks the conductor to call these; never a solver itself, never in another lane.
          </p>
        </fieldset>

        <fieldset className={s.block} disabled={!editable}>
          <legend className={s.eyebrow}>
            Skills{" "}
            <span>
              {cfg.skills.length} of {skills.length} on
            </span>
          </legend>
          {skills.length ? (
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
          ) : (
            <p className={s.hint}>No skills in this room.</p>
          )}
        </fieldset>

        <fieldset className={s.block} disabled={!editable}>
          <legend className={s.eyebrow}>
            Compute budget <span>quick tier</span>
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
          <p className={s.hint}>Solver time per quick run; larger runs go to the accurate queue.</p>
        </fieldset>

        <section className={s.block}>
          <h3 className={s.eyebrow}>
            Watches <span>checked on every result</span>
          </h3>
          {cfg.watches.length ? (
            <ul className={s.rows}>
              {cfg.watches.map((w) => (
                <li key={w.id} className={s.row}>
                  <Icon name="verified" size={14} />
                  <span className={s.rowMain}>
                    {w.label} <span className={s.hint}>{w.case}</span>
                  </span>
                  <span className={s.tag}>
                    {w.onBreak === "propose" ? "proposes when broken" : "warns when broken"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={s.hint}>No watches yet.</p>
          )}
        </section>

        {editable ? (
          <section className={s.block}>
            <h3 className={s.eyebrow}>
              Memory <span>lessons, across all your rooms</span>
            </h3>
            {lessons?.length ? (
              <ul className={s.rows}>
                {lessons.map((l) => (
                  <li key={l.id} className={s.row}>
                    <Icon name="ledger" size={14} />
                    <span className={s.rowMain}>{l.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={s.hint}>
                {lessons === null
                  ? "Loading…"
                  : "No lessons yet. Your reasons on approved or declined proposals become lessons."}
              </p>
            )}
          </section>
        ) : null}

        {editable ? (
          <div className={s.footer}>
            {msg ? (
              <span className={msg.ok ? s.hint : s.bad} role="status">
                {msg.text}
              </span>
            ) : null}
            <Button variant="ghost" small onClick={onBring}>
              <Icon name="a2a" size={14} />
              Bring your own agent
            </Button>
            <Button small disabled={!dirty || busy} onClick={save}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </div>
        ) : (
          <p className={s.hint}>Only {person.name} can change this drawer.</p>
        )}
      </div>
    </Sheet>
  );
}
