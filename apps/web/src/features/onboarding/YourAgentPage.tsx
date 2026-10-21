import { type FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import {
  agentApi,
  cardUrlProblem,
  type MyAgent,
  PROVIDERS,
  type Provider,
} from "@/features/agent/api";
import { safeNext, useAuth } from "@/features/auth";
import { AUTONOMY, type Autonomy, roleInfo } from "@/shared/domain/disciplines";
import { ApiError } from "@/shared/http/client";
import { Button, Icon, Info, seatVar, TextField, ui } from "@/shared/ui";
import { OnboardingShell } from "@/shared/ui/Shell";
import s from "./onboarding.module.css";

export function YourAgentPage({ settings = false }: { settings?: boolean }) {
  const { person } = useAuth();
  const navigate = useNavigate();
  const disc = roleInfo(person?.discipline ?? "");
  const [agent, setAgent] = useState<MyAgent | null>(null);
  const [kind, setKind] = useState<MyAgent["kind"]>("consilith");
  const [provider, setProvider] = useState<Provider>("anthropic");
  const [model, setModel] = useState("");
  const [autonomy, setAutonomy] = useState<Autonomy>(disc.autonomy);
  const [cardUrl, setCardUrl] = useState("");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<{ id: string; label: string }[] | null>(null);
  const [modelsNote, setModelsNote] = useState<string | null>(null);

  // The model list comes from the provider itself, for the key this session holds.
  async function loadModels(p: Provider, newKey?: string) {
    if (p === "scripted") return;
    setModels(null);
    setModelsNote("Loading models…");
    try {
      if (newKey) {
        await agentApi.setKey(p, newKey);
        setKey("");
        setAgent((a) => (a ? { ...a, hasKey: true } : a));
      }
      const r = await agentApi.models(p);
      setModels(r.models);
      setModelsNote(r.models.length ? null : "This key lists no chat models.");
      setModel((m) => (m && r.models.some((x) => x.id === m) ? m : (r.models[0]?.id ?? "")));
    } catch (e) {
      setModelsNote(
        e instanceof ApiError && e.status === 409
          ? "Paste your key to load the models."
          : e instanceof ApiError
            ? e.message
            : "The models could not be loaded.",
      );
    }
  }

  // Sign in with ChatGPT: the local helper holds the session; the page only starts it and waits.
  const [siwc, setSiwc] = useState<{ available: boolean; signedIn: boolean; email?: string }>({
    available: false,
    signedIn: false,
  });
  const [siwcBusy, setSiwcBusy] = useState(false);

  // The saved choice and the ChatGPT plan load together: a connected plan is the default
  // whenever the saved provider cannot run (no model, or no key held for this session).
  useEffect(() => {
    Promise.all([
      agentApi.get(),
      agentApi.siwcStatus().catch(() => ({ available: false, signedIn: false })),
    ]).then(
      ([a, st]) => {
        setAgent(a);
        setSiwc(st);
        setKind(a.kind);
        const unusable = a.provider === "scripted" || (a.provider !== "siwc" && !a.hasKey);
        setProvider(st.signedIn && unusable ? "siwc" : a.provider);
        setModel(a.model ?? "");
        setAutonomy(a.autonomy);
        setCardUrl(a.cardUrl ?? "");
      },
      () => setAgent(null),
    );
  }, []);

  async function connectChatGPT() {
    if (siwc.signedIn) {
      setProvider("siwc");
      return;
    }
    setSiwcBusy(true);
    try {
      const { url } = await agentApi.siwcLogin();
      if (url) window.open(url, "_blank", "noopener");
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const st = await agentApi.siwcStatus();
        if (st.signedIn) {
          setSiwc(st);
          setProvider("siwc");
          return;
        }
      }
      setError("The ChatGPT sign-in did not finish. Try again.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The ChatGPT sign-in could not start.");
    } finally {
      setSiwcBusy(false);
    }
  }

  // biome-ignore lint/correctness/useExhaustiveDependencies: reload only when the provider changes
  useEffect(() => {
    void loadModels(provider);
  }, [provider]);

  if (!person) return null;

  function ModelField() {
    if (provider === "scripted")
      return (
        <TextField label="Model" placeholder="none" value="" disabled onChange={() => undefined} />
      );
    return models?.length ? (
      <label className={ui.field}>
        <span className={ui.label}>Model</span>
        <select
          className={s.select}
          value={model}
          onChange={(e) => {
            setModel(e.target.value);
            setError(null);
          }}
        >
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
            </option>
          ))}
        </select>
      </label>
    ) : (
      <TextField
        label="Model"
        placeholder={modelsNote ?? "the list loads once your key is in"}
        value=""
        disabled
        onChange={() => undefined}
      />
    );
  }
  const color = seatVar(person.color);
  const urlProblem = kind === "brought" ? cardUrlProblem(cardUrl) : null;
  // docs/web-api.md: OpenAI and Gemini need a model name; Anthropic has a default.
  const needsModel = kind === "consilith" && (provider === "openai" || provider === "gemini");
  const modelProblem =
    needsModel && !model.trim() ? "Name the model to use with this provider." : null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (urlProblem || modelProblem) {
      setError(urlProblem ?? modelProblem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await agentApi.save({
        kind,
        provider,
        model:
          kind === "consilith" && provider !== "scripted" && model.trim() ? model.trim() : null,
        autonomy,
        cardUrl: kind === "brought" ? cardUrl.trim() : null,
      });
      if (kind === "consilith" && key.trim() && provider !== "scripted" && provider !== "siwc") {
        await agentApi.setKey(provider, key.trim());
      }
      setKey("");
      setAgent({ ...saved, hasKey: saved.hasKey || Boolean(key.trim()) });
      if (settings) {
        navigate("/");
        return;
      }
      let next = "/";
      try {
        next = safeNext(sessionStorage.getItem("consilith.next"));
        sessionStorage.removeItem("consilith.next");
      } catch {
        /* Home */
      }
      navigate(next);
    } catch (err) {
      setKey("");
      setError(err instanceof ApiError ? err.message : "Saving failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <OnboardingShell
      {...(settings ? {} : { step: 2, of: 2 })}
      label={settings ? "Your agent · settings" : "Your agent"}
    >
      <div>
        <h1 className={s.title}>Who works for you in every room</h1>
        <p className={s.lede}>It edits your lanes and proposes everywhere else.</p>
      </div>
      <form className={s.cols} onSubmit={submit} noValidate>
        <div className={s.form}>
          <fieldset className={s.fieldset}>
            <legend className="visually-hidden">Which agent</legend>
            <div className={s.kinds}>
              <label className={s.kind}>
                <input
                  type="radio"
                  name="kind"
                  className="visually-hidden"
                  checked={kind === "consilith"}
                  onChange={() => setKind("consilith")}
                />
                <Icon name="agent" size={22} accent={color} />
                <strong>A Consilith agent</strong>
                <Info text="Spawned when you join a room, with your discipline's skills and the model you choose." />
              </label>
              <label className={s.kind}>
                <input
                  type="radio"
                  name="kind"
                  className="visually-hidden"
                  checked={kind === "brought"}
                  onChange={() => setKind("brought")}
                />
                <Icon name="a2a" size={22} />
                <strong>Your own A2A agent</strong>
                <Info text="Point to its Agent Card. It gets exactly your permissions, no more." />
              </label>
            </div>
          </fieldset>

          {kind === "brought" ? (
            <div className={s.bring}>
              <TextField
                label="Its Agent Card URL"
                type="url"
                placeholder="https://your-agent.example.org/.well-known/agent-card.json"
                value={cardUrl}
                onChange={(e) => setCardUrl(e.target.value)}
                error={cardUrl && urlProblem ? urlProblem : null}
              />
              <ol className={s.steps}>
                <li>
                  <span>
                    <strong>Serve it over</strong>{" "}
                    <a
                      className={s.learn}
                      href="https://a2a-protocol.org"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      A2A 1.0 ↗
                    </a>
                    . Most A2A SDKs publish its card for you.
                  </span>
                </li>
                <li>
                  <span>
                    <strong>Host it on public https.</strong> The card is at{" "}
                    <code>/.well-known/agent-card.json</code>.
                  </span>
                </li>
                <li>
                  <span>
                    <strong>Paste that URL.</strong> It gets your lanes and permissions, nothing
                    more.
                  </span>
                </li>
              </ol>
            </div>
          ) : (
            <fieldset className={s.fieldset}>
              <legend className={s.legendRow}>
                <span className={s.legend}>
                  Its model{" "}
                  <Info text="It reads, labels and negotiates; it never produces a number. Your key stays in server memory for this session only." />
                </span>
              </legend>
              {siwc.signedIn ? (
                <div className={s.chatgptOn}>
                  <span className={s.chatgptMark} aria-hidden />
                  <button
                    type="button"
                    className={s.chatgptUse}
                    aria-pressed={provider === "siwc"}
                    onClick={() => setProvider("siwc")}
                  >
                    ChatGPT plan{siwc.email ? ` · ${siwc.email}` : ""}
                  </button>
                  <button
                    type="button"
                    className={s.chatgptOff}
                    onClick={async () => {
                      await agentApi.siwcLogout().catch(() => undefined);
                      setSiwc({ available: true, signedIn: false });
                      if (provider === "siwc") setProvider("anthropic");
                    }}
                  >
                    Disconnect
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className={s.chatgpt}
                  disabled={!siwc.available || siwcBusy}
                  aria-pressed={provider === "siwc"}
                  onClick={connectChatGPT}
                >
                  <span className={s.chatgptMark} aria-hidden />
                  {siwcBusy
                    ? "Opening ChatGPT… finish in the new tab"
                    : !siwc.available
                      ? "Continue with ChatGPT · helper not running"
                      : "Continue with ChatGPT"}
                </button>
              )}
              <div className={s.row2}>
                <label className={ui.field}>
                  <span className={ui.label}>Provider</span>
                  <select
                    className={s.select}
                    value={provider}
                    onChange={(e) => setProvider(e.target.value as Provider)}
                  >
                    {PROVIDERS.filter((p) => p.id !== "siwc" || siwc.signedIn).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </label>
                {provider === "siwc" || provider === "scripted" ? (
                  <ModelField />
                ) : (
                  <TextField
                    label="API key"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={
                      agent?.hasKey ? "held for this session" : "paste your key, then Enter"
                    }
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    onBlur={() => key.trim() && void loadModels(provider, key.trim())}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && key.trim()) {
                        e.preventDefault();
                        void loadModels(provider, key.trim());
                      }
                    }}
                  />
                )}
              </div>
              {provider === "siwc" || provider === "scripted" ? (
                <span className={s.hint}>
                  {provider === "siwc"
                    ? "Runs on your ChatGPT plan: no API key needed."
                    : "Scripted replies only: no model and no key."}
                </span>
              ) : (
                <ModelField />
              )}
            </fieldset>
          )}

          <fieldset className={s.fieldset}>
            <legend className={s.legend}>
              How far it goes without asking{" "}
              <Info text="Set by your discipline. Change it any time, here or in the room's agent drawer." />
            </legend>
            <div className={s.segment}>
              {AUTONOMY.map((a) => (
                <label key={a.id} className={s.segOpt} title={`${a.label}: ${a.note}`}>
                  <input
                    type="radio"
                    name="autonomy"
                    className="visually-hidden"
                    checked={autonomy === a.id}
                    onChange={() => setAutonomy(a.id)}
                  />
                  <strong>{a.label}</strong>
                  <span className="visually-hidden">{a.note}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {error ? (
            <span className={s.bad} role="alert">
              {error}
            </span>
          ) : null}
        </div>

        <aside className={s.preview} aria-label="Its agent card, as the room sees it">
          <span className={s.caps}>
            As the room sees it{" "}
            <Info text="Published as an A2A Agent Card, so everyone in a room can see what it may do." />
          </span>
          <div className={s.previewBox}>
            <div className={s.agentCard}>
              <div className={s.agentHead}>
                <Icon name={kind === "brought" ? "a2a" : "agent"} size={32} accent={color} />
                <div>
                  <div className={s.agentName}>
                    {kind === "brought" ? "your A2A agent" : `${person.id}.agent`}
                  </div>
                  <div className={s.small}>Personal agent of {person.name}</div>
                </div>
              </div>
              <div className={s.cardRows}>
                <Icon name="lane" size={18} accent={color} />
                <span>Edits your lanes only</span>
                <Icon name="proposal" size={18} />
                <span>Proposes everywhere else</span>
                <Icon name="drawer" size={18} />
                <span>{AUTONOMY.find((a) => a.id === autonomy)?.label}, up to 60 s per run</span>
              </div>
              {kind === "brought" ? (
                <span className={s.small}>Its skills come from its own Agent Card.</span>
              ) : (
                <div className={s.chips}>
                  {disc.skills.map((k) => (
                    <span key={k} className={s.chip}>
                      {k}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </aside>

        <div className={s.footer}>
          <Button variant="ghost" onClick={() => navigate(settings ? "/" : "/welcome/about")}>
            {settings ? "Cancel" : "Back"}
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : settings ? "Save" : "Finish and go to your rooms"}{" "}
            <span aria-hidden>→</span>
          </Button>
        </div>
      </form>
    </OnboardingShell>
  );
}
