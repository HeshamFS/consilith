import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import {
  agentApi,
  cardUrlProblem,
  loadProviders,
  type ModelOption,
  type MyAgent,
  type Provider,
  type ProviderInfo,
} from "@/features/agent/api";
import { ConnectPanel } from "@/features/agent/ConnectPanel";
import { ModelPicker } from "@/features/agent/ModelPicker";
import { safeNext, useAuth } from "@/features/auth";
import { AUTONOMY, type Autonomy, roleInfo } from "@/shared/domain/disciplines";
import { ApiError, api } from "@/shared/http/client";
import { Button, Icon, Info, seatVar, TextField, ui } from "@/shared/ui";
import { OnboardingShell } from "@/shared/ui/Shell";
import s from "./onboarding.module.css";

/** What the server adds to the ChatGPT status and sign-in answers when several accounts are on. */
type SiwcStatusExtra = {
  signedIn: boolean;
  signingIn?: boolean;
  email?: string;
  reason?: string;
  multi?: boolean;
  offerDisconnect?: boolean;
  error?: { code?: string; message?: string } | null;
};
type SiwcLoginExtra = { url: string; authOrigins?: string[]; pasteCallback?: boolean };

function addressIsTrusted(address: string, origins: string[]): boolean {
  try {
    const u = new URL(address);
    return u.protocol === "https:" && origins.includes(u.origin);
  } catch {
    return false;
  }
}

export function YourAgentPage({ settings = false }: { settings?: boolean }) {
  const { person } = useAuth();
  const navigate = useNavigate();
  const disc = roleInfo(person?.discipline ?? "");
  const [agent, setAgent] = useState<MyAgent | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [providersError, setProvidersError] = useState(false);
  const [kind, setKind] = useState<MyAgent["kind"]>("consilith");
  const [provider, setProvider] = useState<Provider>("scripted");
  const [model, setModel] = useState("");
  const [autonomy, setAutonomy] = useState<Autonomy>(disc.autonomy);
  const [cardUrl, setCardUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<ModelOption[] | null>(null);
  const [modelsNote, setModelsNote] = useState<string | null>(null);
  const [siwc, setSiwc] = useState<{ signedIn: boolean; email?: string }>({ signedIn: false });
  // Set only when the server runs several ChatGPT accounts (the operator flag is on).
  const [multi, setMulti] = useState(false);
  const [canPaste, setCanPaste] = useState(false);
  const [offerDisconnect, setOfferDisconnect] = useState(false);

  const info = providers?.find((p) => p.id === provider);
  const credential = agent?.credential?.byProvider[provider];
  const connected = credential?.state === "ready";

  const refreshAgent = useCallback(async () => {
    setAgent(await agentApi.get());
  }, []);

  const loadProviderList = useCallback(() => {
    setProvidersError(false);
    loadProviders().then(
      (list) => setProviders([...list]),
      () => setProvidersError(true),
    );
  }, []);

  // The registry, the saved choice and the ChatGPT account load together. The saved provider is
  // kept as it is: nothing here switches a person to another provider on its own.
  useEffect(() => {
    loadProviderList();
    Promise.all([agentApi.get(), agentApi.siwcStatus().catch(() => null)]).then(
      ([a, st]) => {
        setAgent(a);
        if (st) {
          setSiwc({ signedIn: st.signedIn, ...(st.email ? { email: st.email } : {}) });
          const extra = st as SiwcStatusExtra;
          setMulti(extra.multi === true);
          setOfferDisconnect(extra.offerDisconnect === true);
        }
        setKind(a.kind);
        setProvider(a.provider);
        setModel(a.model ?? "");
        setAutonomy(a.autonomy);
        setCardUrl(a.cardUrl ?? "");
      },
      () => setAgent(null),
    );
  }, [loadProviderList]);

  // The model list comes from the provider itself, for the connection this session holds.
  useEffect(() => {
    setModels(null);
    if (!info?.needsModel) {
      setModelsNote(null);
      return;
    }
    if (!connected) {
      setModelsNote("connect first, then pick a model");
      return;
    }
    let live = true;
    setModelsNote("Loading models…");
    agentApi.models(provider).then(
      (r) => {
        if (!live) return;
        setModels(r.models);
        setModelsNote(r.models.length ? null : "This connection lists no chat models.");
        setModel((m) => (r.models.some((x) => x.id === m) ? m : ""));
      },
      (e: unknown) => {
        if (live)
          setModelsNote(e instanceof ApiError ? e.message : "The models could not be loaded.");
      },
    );
    return () => {
      live = false;
    };
  }, [provider, info, connected]);

  // Changing the provider clears the model: an id belongs to one maker.
  function chooseProvider(next: Provider) {
    if (next === provider) return;
    setProvider(next);
    setModel("");
    setError(null);
  }

  async function saveKey(key: string) {
    await agentApi.setKey(provider, key);
    await refreshAgent();
  }

  // Sign in with ChatGPT: the local helper holds the session; the page only starts it and waits.
  async function connectChatGPT() {
    const login = (await agentApi.siwcLogin()) as SiwcLoginExtra;
    const { url } = login;
    // With several accounts the server names the origins the address may be on. Open nothing else.
    if (login.authOrigins && url && !addressIsTrusted(url, login.authOrigins)) {
      throw new ApiError(0, "bad_login_url", "The ChatGPT sign-in address was not trusted.");
    }
    setCanPaste(login.pasteCallback === true);
    if (url) window.open(url, "_blank", "noopener");
    for (let i = 0; i < 150; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const st = (await agentApi.siwcStatus()) as SiwcStatusExtra;
      if (st.signedIn) {
        setSiwc({ signedIn: true, ...(st.email ? { email: st.email } : {}) });
        setCanPaste(false);
        await refreshAgent();
        return;
      }
      // A refusal ends the wait at once and says why: the same ChatGPT account for two people,
      // a sign-in that took too long, another person signing in. With the account map off the
      // page keeps polling as it always did.
      if (st.multi === true && !st.signingIn && (st.reason || st.error?.message)) {
        setCanPaste(false);
        throw new ApiError(
          409,
          st.error?.code ?? "signin_refused",
          st.reason ?? st.error?.message ?? "",
        );
      }
    }
    setCanPaste(false);
    throw new ApiError(0, "signin_timeout", "The ChatGPT sign-in did not finish. Try again.");
  }

  async function pasteCallback(address: string) {
    await api.post<{ accepted: true }>("/me/agent/siwc/callback", { callbackUrl: address });
  }

  async function disconnectChatGPT() {
    await agentApi.siwcLogout();
    setSiwc({ signedIn: false });
    setOfferDisconnect(false);
    await refreshAgent();
  }

  if (!person) return null;

  const color = seatVar(person.color);
  const urlProblem = kind === "brought" ? cardUrlProblem(cardUrl) : null;
  // Every real provider needs a model chosen from its list; none is filled in for the person.
  const needsModel = kind === "consilith" && Boolean(info?.needsModel);
  const modelProblem =
    needsModel && !model.trim() ? "Choose the model to use with this provider." : null;

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
        model: needsModel && model.trim() ? model.trim() : null,
        autonomy,
        cardUrl: kind === "brought" ? cardUrl.trim() : null,
        hourlyCap: agent?.hourlyCap ?? null,
      });
      setAgent(saved);
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
              <div className={s.row2}>
                <label className={ui.field}>
                  <span className={ui.label}>Provider</span>
                  <select
                    className={s.select}
                    value={provider}
                    onChange={(e) => chooseProvider(e.target.value)}
                  >
                    {(providers ?? []).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </label>
                <ModelPicker
                  models={models}
                  value={model}
                  onChange={(m) => {
                    setModel(m);
                    setError(null);
                  }}
                  note={info?.needsModel ? modelsNote : "no model needed"}
                  className={s.select}
                />
              </div>
              {info ? (
                <ConnectPanel
                  provider={info}
                  credential={credential}
                  {...(info.auth === "key" ? { onSaveKey: saveKey } : {})}
                  {...(info.auth === "chatgpt"
                    ? {
                        onConnectChatGPT: connectChatGPT,
                        onDisconnectChatGPT: disconnectChatGPT,
                        ...(siwc.email ? { email: siwc.email } : {}),
                        ...(multi ? { multi: true } : {}),
                        ...(multi && canPaste ? { onPasteCallback: pasteCallback } : {}),
                        ...(offerDisconnect ? { offerDisconnect: true } : {}),
                      }
                    : {})}
                />
              ) : providersError ? (
                <span className={s.bad} role="alert">
                  The provider list could not be loaded.{" "}
                  <button type="button" className={s.learn} onClick={loadProviderList}>
                    Try again
                  </button>
                </span>
              ) : (
                <span className={s.hint}>Loading providers…</span>
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
