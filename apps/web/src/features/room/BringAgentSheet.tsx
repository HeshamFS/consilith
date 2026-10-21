import { useState } from "react";
import { Button, Icon, seatVar, TextField } from "@/shared/ui";
import s from "./drawer.module.css";
import { Sheet } from "./Sheet";

export type CardSummary = {
  name: string;
  description?: string;
  provider?: string;
  version?: string;
  skills: string[];
};

type Check = { text: string; ok: boolean | null; why?: string };

function privateHost(h: string): boolean {
  return /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|\[?::1\]?$|\[?f[cd]|0\.)/i.test(
    h,
  );
}

/** The checks the room server runs, shown live as the URL is typed. The last ones only the
 * server can answer, so they stay pending until it does. */
export function checksFor(url: string, server: "idle" | "ok" | "refused"): Check[] {
  const m = /^([a-z]+):\/\/([^/?#]*)([^?#]*)/i.exec(url.trim()) ?? [];
  const scheme = (m[1] ?? "").toLowerCase();
  const auth = m[2] ?? "";
  const path = m[3] ?? "";
  const host = auth.replace(/^.*@/, "").replace(/:\d+$/, "");
  const typed = url.trim().length > 0;
  const live = (ok: boolean) => (typed ? ok : null);
  const after = server === "idle" ? null : server === "ok";
  return [
    { text: "Starts with https://", ok: live(scheme === "https"), why: "only https is allowed." },
    {
      text: "No user name or password in the URL",
      ok: live(Boolean(auth) && !auth.includes("@")),
      why: "credentials in a URL are never sent.",
    },
    {
      text: "Ends with /.well-known/agent-card.json",
      ok: live(/\/\.well-known\/agent-card\.json$/.test(path)),
      why: "that is not an Agent Card address.",
    },
    {
      text: "Host is not a private or local address",
      ok: live(Boolean(host) && !privateHost(host)),
      why: "the room will not call a private or local address.",
    },
    { text: "Resolves to a public address, no redirects followed", ok: after },
    { text: "Card under 2 MB, fetched within 60 s", ok: after },
    { text: "A2A 1.0, every interface URL passes the same checks", ok: after },
  ];
}

export function BringAgentSheet({
  person,
  current,
  onFetch,
  onReplace,
  onBack,
  onClose,
}: {
  person: { id: string; name: string; color: string };
  current: { name: string; kind: "consilith" | "brought"; note: string };
  onFetch: (url: string) => Promise<CardSummary>;
  onReplace: (url: string) => Promise<void>;
  onBack: () => Promise<void>;
  onClose: () => void;
}) {
  const [url, setUrl] = useState("");
  const [server, setServer] = useState<"idle" | "ok" | "refused">("idle");
  const [card, setCard] = useState<CardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"fetch" | "replace" | "back" | null>(null);
  const checks = checksFor(url, server);
  const localOk = checks.slice(0, 4).every((c) => c.ok);
  const me = seatVar(person.color);

  async function fetchCard() {
    setBusy("fetch");
    setError(null);
    setCard(null);
    try {
      const c = await onFetch(url.trim());
      setCard(c);
      setServer("ok");
    } catch (e) {
      setServer("refused");
      setError(e instanceof Error ? e.message : "The room refused that card.");
    } finally {
      setBusy(null);
    }
  }

  async function act(kind: "replace" | "back") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "replace") await onReplace(url.trim());
      else await onBack();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Sheet title="Your agent" sub="Consilith's, or your own over A2A" narrow onClose={onClose}>
      <div className={s.stack}>
        <div className={s.current} style={{ borderColor: me }}>
          <Icon name={current.kind === "brought" ? "a2a" : "agent"} size={28} accent={me} />
          <div>
            <div className={s.strong}>{current.name}</div>
            <div className={s.small}>{current.note}</div>
          </div>
          {current.kind === "brought" ? (
            <Button variant="ghost" small disabled={busy !== null} onClick={() => act("back")}>
              {busy === "back" ? "Saving…" : `Use ${person.id}.agent in new rooms`}
            </Button>
          ) : null}
        </div>

        <h3 className={s.h3}>Bring your own A2A agent</h3>
        <TextField
          label="Its Agent Card URL"
          type="url"
          inputMode="url"
          spellCheck={false}
          placeholder="https://…/.well-known/agent-card.json"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setServer("idle");
            setCard(null);
            setError(null);
          }}
        />
        <ul className={s.checks} aria-label="Address checks">
          {checks.map((c) => (
            <li key={c.text} data-ok={c.ok === null ? undefined : String(c.ok)}>
              <span aria-hidden className={s.mark}>
                {c.ok === null ? "·" : c.ok ? "✓" : "✕"}
              </span>
              <span>
                {c.text}
                {c.ok === false && c.why ? <span className={s.why}> No: {c.why}</span> : null}
              </span>
              <span className="visually-hidden">
                {c.ok === null ? "not checked yet" : c.ok ? "passes" : "fails"}
              </span>
            </li>
          ))}
        </ul>
        {error ? (
          <p className={s.bad} role="alert">
            {error}
          </p>
        ) : null}
        <Button variant="secondary" disabled={!localOk || busy !== null} onClick={fetchCard}>
          {busy === "fetch" ? "Fetching the card…" : "Fetch its card and bring it in"}
        </Button>

        {card ? (
          <section className={s.card} aria-label="The fetched Agent Card">
            <div className={s.cardHead}>
              <Icon name="a2a" size={24} />
              <span className={s.strong}>{card.name}</span>
              <span className={s.small}>A2A {card.version ?? "1.0"}</span>
            </div>
            <dl className={s.dl}>
              <dt>Provider</dt>
              <dd>{card.provider ?? "not stated"}</dd>
              <dt>Skills</dt>
              <dd>{card.skills.length ? card.skills.join(", ") : "none listed"}</dd>
              <dt>Will get</dt>
              <dd>{person.name}'s lanes and permissions, nothing more</dd>
            </dl>
            <p className={s.small} role="status">
              {card.name} now works for you in this room, in place of {person.id}.agent.
            </p>
            <Button
              accent={me}
              variant="accent"
              disabled={busy !== null}
              onClick={() => act("replace")}
            >
              {busy === "replace" ? "Saving…" : `Also bring ${card.name} to new rooms`}
            </Button>
          </section>
        ) : null}

        <p className={s.small}>
          The room never follows redirects and refuses loopback, private, link-local and reserved
          addresses. Your permissions stay the same: a brought agent can do exactly what your own
          agent could.
        </p>
      </div>
    </Sheet>
  );
}
