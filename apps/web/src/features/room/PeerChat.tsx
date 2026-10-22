import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from "react";
import { Avatar, Icon, seatVar } from "@/shared/ui";
import { agentModelLabel } from "./agentLabel";
import { nameOf } from "./describe";
import { MAX_HOPS, type PeerEntry, type PeerWaiting, useMyPeerFeed } from "./peer";
import s from "./peer-chat.module.css";
import { ThinkingMark, useReducedMotion } from "./ThinkingMark";
import type { RoomDoc } from "./types";

/** One chat for every agent in the room (Hesham, 2026-10-22): pick an agent and talk to it; each
 * agent speaks in its owner's seat colour, with the API and model it runs on beside its name, so
 * two providers talking to each other are plainly two providers. */

export type RoomAgentChoice = {
  aid: string;
  person: string;
  name: string;
  color: string;
  label: string;
  mine: boolean;
};

/** Every personal agent in the room, your own first. */
export function roomAgents(doc: RoomDoc | null | undefined, me: string): RoomAgentChoice[] {
  if (!doc?.room.agents) return [];
  const out: RoomAgentChoice[] = [];
  for (const a of Object.values(doc.room.agents)) {
    if (a.kind !== "personal") continue;
    out.push({
      aid: a.id,
      person: a.owner,
      name: a.brought ? (a.card?.name ?? a.id) : a.id,
      color: seatVar(doc.room.people?.[a.owner]?.color),
      label: agentModelLabel(doc, a.id),
      mine: a.owner === me,
    });
  }
  return out.sort((x, y) => Number(y.mine) - Number(x.mine) || x.person.localeCompare(y.person));
}

/** A finished `@name ` at the start of the text, or after a space, that names an agent in the
 * room (by person id, person name or agent id): that agent, and the text without the mention. */
export function mentionOf(
  text: string,
  agents: RoomAgentChoice[],
  doc: RoomDoc | null | undefined,
): { agent: RoomAgentChoice; rest: string } | null {
  const m = /(^|\s)@([\w.-]+)\s/.exec(text);
  if (!m) return null;
  const word = (m[2] ?? "").toLowerCase().replace(/\.agent$/, "");
  const agent = agents.find(
    (a) =>
      a.person.toLowerCase() === word ||
      nameOf(a.person, doc ?? null).toLowerCase() === word ||
      a.aid.toLowerCase() === word,
  );
  if (!agent) return null;
  const rest = (text.slice(0, m.index) + (m[1] ?? "") + text.slice(m.index + m[0].length)).trim();
  return { agent, rest };
}

/** "OpenAI API · gpt-6.1-sol" as a small mono chip. */
export function ModelChip({ label }: { label: string }) {
  if (!label) return null;
  return (
    <span className={s.model} data-testid="model-label">
      {label}
    </span>
  );
}

export function AgentPicker({
  agents,
  target,
  onPick,
}: {
  agents: RoomAgentChoice[];
  target: string;
  onPick: (aid: string) => void;
}) {
  if (agents.length < 2) return null;
  return (
    <fieldset className={s.picker}>
      <legend className="visually-hidden">Talk to</legend>
      {agents.map((a) => (
        <button
          key={a.aid}
          type="button"
          aria-pressed={a.aid === target}
          className={s.pick}
          style={{ "--c": a.color } as CSSProperties}
          onClick={() => onPick(a.aid)}
        >
          <span className={s.dot} aria-hidden />
          <span className={s.pickName}>{a.mine ? `${a.name} · yours` : a.name}</span>
          <ModelChip label={a.label} />
        </button>
      ))}
    </fieldset>
  );
}

const KIND_WORD: Record<string, string> = {
  question: "asks",
  answer: "answers",
  fyi: "notes",
  handoff: "hands over",
};

/** One agent-to-agent message, in the speaking agent's colour, with its model. */
const RESULT_ID = /\bres-[0-9a-f]{8,}\b/g;

/** Result ids in an agent's message read as the result's name ("segmentation review"), with
 * the id in the tooltip; an unknown id stays as written. */
export function withResultNames(text: string, doc: RoomDoc | null | undefined): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(RESULT_ID)) {
    const id = match[0];
    const at = match.index ?? 0;
    const record = doc?.results?.[id];
    if (!record) continue;
    if (at > last) out.push(text.slice(last, at));
    out.push(
      <span key={`${id}:${at}`} className={s.resultRef} title={id}>
        {record.quantity.replaceAll("_", " ")}
      </span>,
    );
    last = at + id.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** One agent-to-agent message as a chat bubble, seen from ``me``: my side (me or my agent) on the
 * right in my seat colour, the other side on the left in theirs, like the person's own bubble.
 * The agent and its API and model sit small above the bubble. */
/** A run-status note the room posted into a thread ("Room update: … the run finished"): a fyi
 * from no seated person, or one the room words as an update. */
export function isRoomNote(m: PeerEntry, doc: RoomDoc | null | undefined): boolean {
  if (m.kind !== "fyi") return false;
  return (
    /^room update\b/i.test(m.text) ||
    m.from.agent === "conductor" ||
    !doc?.room.people?.[m.from.person]
  );
}

/** A room note as one neutral system line, never a coloured bubble. */
export function RoomNote({ m, doc }: { m: PeerEntry; doc: RoomDoc | null | undefined }) {
  return (
    <p className={s.system} data-testid="room-note">
      <Icon name="conductor" size={12} />
      <span>{withResultNames(m.text.replace(/^room update:\s*/i, ""), doc)}</span>
    </p>
  );
}

export function PeerLine({
  doc,
  m,
  me,
}: {
  doc: RoomDoc | null | undefined;
  m: PeerEntry;
  me?: string | undefined;
}) {
  if (isRoomNote(m, doc)) return <RoomNote m={m} doc={doc} />;
  const color = seatVar(doc?.room.people?.[m.from.person]?.color);
  const side = me && m.from.person === me ? "mine" : "theirs";
  return (
    <article
      className={s.line}
      data-side={side}
      style={{ "--c": color } as CSSProperties}
      data-testid="peer-line"
      data-agent={m.from.agent}
      aria-label={`${m.from.agent} ${KIND_WORD[m.kind] ?? m.kind}`}
    >
      <header className={s.byline}>
        <span className={s.agent}>{m.from.agent}</span>
        <ModelChip label={agentModelLabel(doc, m.from.agent)} />
        <span className={s.hop} title={`${KIND_WORD[m.kind] ?? m.kind} ${m.to.agent}`}>
          hop {m.hop}/{MAX_HOPS}
        </span>
      </header>
      <p className={s.bubble}>{withResultNames(m.text, doc)}</p>
    </article>
  );
}

export function PeerReplying({ doc, agent }: { doc: RoomDoc | null | undefined; agent: string }) {
  const person = agent.replace(/\.agent$/, "");
  const color = seatVar(doc?.room.people?.[person]?.color);
  return (
    <div
      className={s.replying}
      style={{ "--c": color } as CSSProperties}
      data-testid="peer-replying"
    >
      <ThinkingMark thinking size={16} />
      <span>
        {agent} is replying <ModelChip label={agentModelLabel(doc, agent)} />
      </span>
    </div>
  );
}

/** How long the pop-up stays after the exchange goes quiet. */
export const POPUP_QUIET_MS = 20_000;
/** "Mute for 5 min": nothing from these agents pops up until then. */
export const POPUP_MUTE_S = 300;
/** A message longer than this is clamped to a few lines with "more". */
const LONG = 160;

const PRIVACY = "Everyone in this room can read this thread.";

/** An agent's face: its owner's avatar in their seat colour, with the agent mark on it. */
function Face({ doc, person }: { doc: RoomDoc | null | undefined; person: string }) {
  const p = doc?.room.people?.[person];
  return (
    <span className={s.face} style={{ "--c": seatVar(p?.color) } as CSSProperties}>
      <Avatar name={p?.name || person} color={p?.color ?? ""} size={22} />
      <span className={s.faceMark}>
        <Icon name="agent" size={9} />
      </span>
    </span>
  );
}

/** A small notification on the screen of each person in a thread when a message arrives that
 * they did not send: who is talking to whom on which models, the newest message, who is replying,
 * "Open in chat", and a five-minute mute. History at load never opens it. */
export function PeerPopup({
  doc,
  me,
  onOpenChat,
}: {
  doc: RoomDoc | null | undefined;
  me: string;
  onOpenChat: (thread: string) => void;
}) {
  const feed = useMyPeerFeed(doc, me);
  const reduced = useReducedMotion();
  // Anything already in the threads when the room opened is history.
  const since = useRef(Date.now() / 1000 - 2);
  const [closedAt, setClosedAt] = useState(0);
  const [hover, setHover] = useState(false);
  const [quiet, setQuiet] = useState(false);
  const [more, setMore] = useState(false);

  const incoming = feed.entries.filter(
    (m) => m.ts > since.current && m.ts > closedAt && m.from.person !== me,
  );
  const latest = incoming[incoming.length - 1];
  const thread = latest?.thread ?? null;
  const inThread = thread ? feed.entries.filter((m) => m.thread === thread) : [];
  const newest = inThread[inThread.length - 1];
  const earlier = Math.max(0, inThread.length - 1);
  const waiting: PeerWaiting | undefined = feed.waiting.find((w) => w.thread === thread);
  const lastTs = newest?.ts ?? 0;
  const activity = `${lastTs}:${waiting?.agent ?? ""}`;

  // The exchange went quiet: close after POPUP_QUIET_MS unless the person is reading it.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `activity` restarts the quiet clock
  useEffect(() => {
    setQuiet(false);
    if (!thread || waiting || hover) return;
    const t = setTimeout(() => setQuiet(true), POPUP_QUIET_MS);
    return () => clearTimeout(t);
  }, [thread, activity, hover, waiting]);

  // A new message is read short again.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `lastTs` is what makes it new
  useEffect(() => setMore(false), [lastTs]);

  if (!thread || quiet || !newest) return null;
  const close = () => setClosedAt(Math.max(lastTs, Date.now() / 1000));
  const mute = () => setClosedAt(Date.now() / 1000 + POPUP_MUTE_S);
  const open = () => {
    onOpenChat(thread);
    close();
  };
  const note = isRoomNote(newest, doc);
  const from = note && latest ? latest : newest;
  const sender = seatVar(doc?.room.people?.[from.from.person]?.color);
  const long = !note && newest.text.length > LONG;
  return (
    <aside
      className={s.popup}
      data-motion={reduced ? "reduced" : "full"}
      style={{ "--c": sender } as CSSProperties}
      aria-label="Agents are talking"
      data-testid="peer-popup"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <header className={s.popHead}>
        <span className={s.pair} aria-hidden>
          <Face doc={doc} person={from.from.person} />
          <Face doc={doc} person={from.to.person} />
        </span>
        <span className={s.popWho}>
          <span className={s.popLine}>
            {from.from.agent} → {from.to.agent}
          </span>
          <span className={s.popModels}>
            <ModelChip label={agentModelLabel(doc, from.from.agent)} />
            <ModelChip label={agentModelLabel(doc, from.to.agent)} />
          </span>
        </span>
        <button type="button" className={s.popClose} aria-label="Close" onClick={close}>
          <svg width="12" height="12" viewBox="0 0 24 24" aria-hidden>
            <path
              d="M6 6l12 12M18 6L6 18"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </header>
      <div className={s.popBody} data-clamped={long && !more ? "true" : undefined}>
        <PeerLine doc={doc} m={newest} me={me} />
        {long ? (
          <button type="button" className={s.popMore} onClick={() => setMore((v) => !v)}>
            {more ? "less" : "more"}
          </button>
        ) : null}
        {waiting ? <PeerReplying doc={doc} agent={waiting.agent} /> : null}
        {earlier ? (
          <button type="button" className={s.popEarlier} onClick={open}>
            +{earlier} earlier
          </button>
        ) : null}
      </div>
      <footer className={s.popFoot}>
        <button type="button" className={s.popOpen} onClick={open}>
          Open in chat
        </button>
        <button type="button" className={s.popMute} onClick={mute}>
          Mute for 5 min
        </button>
        <span className={s.popEye} title={PRIVACY} role="img" aria-label={PRIVACY}>
          <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden>
            <path
              d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
            />
            <circle cx="12" cy="12" r="2.8" fill="none" stroke="currentColor" strokeWidth="1.7" />
          </svg>
        </span>
      </footer>
    </aside>
  );
}
