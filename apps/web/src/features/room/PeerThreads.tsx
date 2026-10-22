import { type CSSProperties, useCallback, useEffect, useId, useState } from "react";
import { Avatar, Icon, seatVar } from "@/shared/ui";
import { agentModelLabel } from "./agentLabel";
import { ago } from "./describe";
import { ModelChip, withResultNames } from "./PeerChat";
import {
  agentLabel,
  closeThread,
  externalAllowed,
  hopCounter,
  involves,
  messageCounter,
  otherOf,
  ownerLabel,
  type PeerMessage,
  type PeerNotice,
  type PeerPrefs,
  type PeerProposal,
  type PeerThread,
  type PeerThreadWire,
  peerThreadsOf,
  prefsOf,
  proposalsAfter,
  savePrefs,
  threadState,
  useFreshIds,
  useThreadWire,
} from "./peer";
import s from "./peer.module.css";
import type { RoomDoc } from "./types";

const KIND_WORDS: Record<string, string> = {
  question: "asks",
  answer: "answers",
  fyi: "shares",
  handoff: "hands over",
};

function colorOf(doc: RoomDoc, person: string): string {
  return doc.room.people[person]?.color ?? "";
}

function seat(doc: RoomDoc, person: string): CSSProperties {
  return { "--c": seatVar(colorOf(doc, person)) } as CSSProperties;
}

/** The hop counter against the 4-hop cap: one segment per hop, the last one turns amber when
 * the exchange has to stop there. */
export function HopMeter({ thread }: { thread: PeerThread }) {
  const hops = hopCounter(thread);
  return (
    <span
      className={s.hops}
      role="img"
      aria-label={`Hop ${hops.value} of ${hops.max}${hops.atCap ? ", the limit" : ""}`}
    >
      {Array.from({ length: hops.max }, (_, i) => (
        <span
          // the segments never reorder
          // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length decoration
          key={i}
          className={s.hop}
          data-on={i < hops.value ? "1" : "0"}
          data-cap={hops.atCap && i === hops.max - 1 ? "1" : "0"}
        />
      ))}
    </span>
  );
}

function ThreadNotice({ notice, state }: { notice: PeerNotice; state: string }) {
  return (
    <p className={s.notice} data-code={notice.code} data-state={state} role="status">
      <Icon name={notice.code === "closed" ? "log" : "warning"} size={14} />
      <span>{notice.text}</span>
    </p>
  );
}

function Dots() {
  return (
    <span className={s.dots} aria-hidden>
      <i />
      <i />
      <i />
    </span>
  );
}

/** A live "replying" bubble while a delivery is pending. */
function Replying({ doc, thread }: { doc: RoomDoc; thread: PeerThread }) {
  const agent = thread.pending?.agent ?? "";
  const person = agent.replace(/\.agent$/, "");
  return (
    <li
      className={`${s.row} ${s.rise}`}
      data-side={thread.participants[0] === person ? "left" : "right"}
    >
      <div className={`${s.bubble} ${s.ghost}`} style={seat(doc, person)} role="status">
        <Dots />
        <span className={s.ghostText}>{agentLabel(doc, agent)} is replying</span>
      </div>
    </li>
  );
}

function ProposalCard({
  doc,
  p,
  onOpenLane,
}: {
  doc: RoomDoc;
  p: PeerProposal;
  onOpenLane?: ((caseId: string, lane: string) => void) | undefined;
}) {
  const live = doc.proposals[p.id]?.status ?? p.status;
  const owner = p.owner ? ownerLabel(doc, p.owner) : "the lane owner";
  const states: Record<string, string> = {
    "awaiting-owner": `waiting for ${owner}`,
    accepted: "accepted",
    declined: "declined",
    withdrawn: "withdrawn",
    failed: "failed",
  };
  return (
    <div className={`${s.proposal} ${s.rise}`} data-status={live}>
      <Icon name="proposal" size={14} />
      <span className={s.proposalText}>
        <span className={s.strong}>{agentLabel(doc, p.by)} proposed a change</span>
        <span className={s.small}>
          {p.case}/{p.lane} · {states[live] ?? live}
        </span>
      </span>
      {onOpenLane ? (
        <button type="button" className={s.link} onClick={() => onOpenLane(p.case, p.lane)}>
          Open lane
        </button>
      ) : null}
    </div>
  );
}

function Bubble({
  doc,
  m,
  side,
  fresh,
  index,
  proposals,
  onOpenLane,
}: {
  doc: RoomDoc;
  m: PeerMessage;
  side: "left" | "right";
  fresh: boolean;
  index: number;
  proposals: PeerProposal[];
  onOpenLane?: ((caseId: string, lane: string) => void) | undefined;
}) {
  const sender = ownerLabel(doc, m.from.person);
  const asker = ownerLabel(doc, m.to.person);
  const auto = m.hop > 1;
  const style = { ...seat(doc, m.from.person), "--i": Math.min(index, 8) } as CSSProperties;
  return (
    <li className={`${s.row} ${fresh ? s.rise : s.settle}`} data-side={side} style={style}>
      <article className={s.bubble} aria-label={`${agentLabel(doc, m.from.agent)}, hop ${m.hop}`}>
        <header className={s.byline}>
          <span className={s.bylineName}>{agentLabel(doc, m.from.agent)}</span>
          <span className={s.bylineFor}>for {sender}</span>
          <ModelChip label={agentModelLabel(doc, m.from.agent)} />
          <span className={s.kind} data-kind={m.kind}>
            {KIND_WORDS[m.kind] ?? m.kind}
          </span>
        </header>
        <p className={s.text}>{withResultNames(m.text, doc)}</p>
        {m.refs.length ? (
          <ul className={s.refs} aria-label="References">
            {m.refs.map((r) => (
              <li key={`${r.kind}:${r.case ?? ""}:${r.id}`}>
                {r.kind === "lane" ? `${r.case}/${r.id}` : r.id}
              </li>
            ))}
          </ul>
        ) : null}
        <footer className={s.foot}>
          <span>hop {m.hop}</span>
          <span aria-hidden>·</span>
          <span>
            {auto
              ? `answered automatically by ${agentLabel(doc, m.from.agent)} at ${asker}'s request`
              : `sent at ${sender}'s request`}
          </span>
          <span aria-hidden>·</span>
          <time>{ago(m.ts)}</time>
        </footer>
        {proposals.map((p) => (
          <ProposalCard key={p.id} doc={doc} p={p} onOpenLane={onOpenLane} />
        ))}
      </article>
    </li>
  );
}

function Party({
  doc,
  person,
  aid,
  live,
}: {
  doc: RoomDoc;
  person: string;
  aid: string;
  live: boolean;
}) {
  return (
    <span className={s.party} style={seat(doc, person)} data-live={live ? "1" : "0"}>
      <span className={s.partyAvatar}>
        <Avatar name={ownerLabel(doc, person)} color={colorOf(doc, person)} size={24} />
      </span>
      <span className={s.partyText}>
        <span className={s.strong}>{agentLabel(doc, aid)}</span>
        <span className={s.small}>for {ownerLabel(doc, person)}</span>
        <ModelChip label={agentModelLabel(doc, aid)} />
      </span>
    </span>
  );
}

/** One thread: the two agents with a wire between them, the counters against the caps, the
 * conversation, and the controls. Works from 460 px up. */
export function ThreadView({
  doc,
  thread,
  me,
  onOpenLane,
  onClose,
}: {
  doc: RoomDoc;
  thread: PeerThread;
  me?: string | null | undefined;
  onOpenLane?: ((caseId: string, lane: string) => void) | undefined;
  onClose?: (() => void) | undefined;
}) {
  const room = doc.room.id;
  const load = useThreadWire(room, thread);
  const wire: PeerThreadWire | null = load.wire;
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [a, b] = thread.participants;
  const [agentA, agentB] = thread.agents;
  const hops = hopCounter(thread);
  const msgs = messageCounter(thread);
  const state = threadState(thread);
  const pendingPerson = thread.pending?.agent.replace(/\.agent$/, "");
  const mine = involves(thread, me);
  const other = otherOf(thread, me);
  const prefs = me ? prefsOf(doc, me) : null;
  const muted = !!prefs?.muted.includes(other);
  const messages = wire?.messages ?? [];
  const fresh = useFreshIds(
    messages.map((m) => m.id),
    thread.id,
  );
  const leftPerson = me && thread.participants.includes(me) ? other : a;

  const run = useCallback(async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    setProblem(null);
    try {
      await fn();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }, []);

  return (
    <section className={s.thread} aria-label="Agent thread" data-state={state}>
      <header className={s.threadHead}>
        <div className={s.parties}>
          <Party doc={doc} person={a ?? ""} aid={agentA ?? ""} live={pendingPerson === a} />
          <span
            className={s.wire}
            data-live={thread.pending ? "1" : "0"}
            data-to={pendingPerson === a ? "left" : "right"}
            aria-hidden
          >
            <span className={s.wireLine} />
          </span>
          <Party doc={doc} person={b ?? ""} aid={agentB ?? ""} live={pendingPerson === b} />
        </div>
        <div className={s.meters} data-row="true">
          <span className={s.meter}>
            <HopMeter thread={thread} />
            <span className={s.meterText}>
              Hop {hops.value} of {hops.max}
            </span>
          </span>
          <span className={s.meter}>
            <span
              className={s.bar}
              role="img"
              aria-label={`${msgs.value} of ${msgs.max} messages`}
              data-cap={msgs.atCap ? "1" : "0"}
            >
              <span className={s.fill} style={{ width: `${(msgs.value / msgs.max) * 100}%` }} />
            </span>
            <span className={s.meterText}>
              {msgs.value} of {msgs.max} messages
            </span>
          </span>
          <p className={s.privacy}>
            <Icon name="room" size={12} />
            Everyone in this room can read this thread.
          </p>
        </div>
      </header>

      {thread.notice ? <ThreadNotice notice={thread.notice} state={state} /> : null}
      {load.status === "error" && !wire ? (
        <p className={s.notice} data-code="refused" role="alert">
          <Icon name="warning" size={14} />
          <span>The messages did not load. {load.message}</span>
        </p>
      ) : null}

      <ol
        className={s.convo}
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="Messages"
      >
        {load.status === "loading" && !wire ? (
          <li className={s.loading}>
            <Dots />
            <span>Opening the thread</span>
          </li>
        ) : null}
        {messages.map((m, i) => (
          <Bubble
            key={m.id}
            doc={doc}
            m={m}
            side={m.from.person === leftPerson ? "left" : "right"}
            fresh={fresh.has(m.id)}
            index={i}
            proposals={proposalsAfter(wire, m)}
            onOpenLane={onOpenLane}
          />
        ))}
        {thread.pending && thread.status === "open" ? <Replying doc={doc} thread={thread} /> : null}
      </ol>

      {mine && thread.status === "open" ? (
        <div className={s.actions}>
          <button
            type="button"
            className={s.ghostButton}
            disabled={busy !== null}
            onClick={() => void run("close", () => closeThread(room, thread.id)).then(onClose)}
          >
            Close thread
          </button>
          <button
            type="button"
            className={s.ghostButton}
            disabled={busy !== null || !me}
            aria-pressed={muted}
            onClick={() =>
              void run("mute", () =>
                savePrefs(room, {
                  muted: muted
                    ? (prefs?.muted ?? []).filter((x) => x !== other)
                    : [...(prefs?.muted ?? []), other],
                }),
              )
            }
          >
            {muted
              ? `Unmute ${ownerLabel(doc, other)}'s agent`
              : `Mute ${ownerLabel(doc, other)}'s agent`}
          </button>
        </div>
      ) : null}
      {problem ? (
        <p className={s.notice} data-code="refused" role="alert">
          <Icon name="warning" size={14} />
          <span>{problem}</span>
        </p>
      ) : null}
    </section>
  );
}

function Switch({
  label,
  hint,
  on,
  busy,
  onChange,
}: {
  label: string;
  hint: string;
  on: boolean;
  busy: boolean;
  onChange: (next: boolean) => void;
}) {
  const id = useId();
  return (
    <div className={s.switchRow}>
      <span className={s.switchText}>
        <span id={id} className={s.strong}>
          {label}
        </span>
        <span className={s.small}>{hint}</span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-labelledby={id}
        className={s.switch}
        data-on={on ? "1" : "0"}
        disabled={busy}
        onClick={() => onChange(!on)}
      >
        <span className={s.knob} />
      </button>
    </div>
  );
}

/** Your side of the consent rules: whether colleagues' agents may ask yours (off until you say
 * so), a pause switch, and whether a proposal a peer caused may teach your agent. */
export function PeerSettings({ doc, me }: { doc: RoomDoc; me: string }) {
  const room = doc.room.id;
  // What the room document says, with a switch shown at its new position while it saves.
  const [shown, setShown] = useState<Partial<PeerPrefs>>({});
  const local: PeerPrefs = { ...prefsOf(doc, me), ...shown };
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const set = async (key: "accepts" | "paused" | "lessons", next: boolean) => {
    setShown((p) => ({ ...p, [key]: next }));
    setBusy(true);
    setProblem(null);
    try {
      await savePrefs(room, { [key]: next });
      setShown((p) => {
        const { [key]: _done, ...rest } = p;
        return rest;
      });
    } catch (e) {
      setShown((p) => {
        const { [key]: _undo, ...rest } = p;
        return rest;
      });
      setProblem(e instanceof Error ? e.message : "That did not save.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className={s.settings} aria-label="Your agent and colleagues' agents">
      <Switch
        label="Let colleagues' agents ask my agent"
        hint={
          local.accepts
            ? "On. Your agent answers on your own model plan, and only while you are signed in."
            : "Off. Nobody's agent can write to yours until you turn this on."
        }
        on={local.accepts}
        busy={busy}
        onChange={(v) => void set("accepts", v)}
      />
      <Switch
        label="Pause peers"
        hint="Stops delivery at once, whatever is already waiting."
        on={local.paused}
        busy={busy}
        onChange={(v) => void set("paused", v)}
      />
      <Switch
        label="Learn from peer-caused proposals"
        hint="Off: a decision on a proposal a colleague's agent caused teaches your agent nothing."
        on={local.lessons}
        busy={busy}
        onChange={(v) => void set("lessons", v)}
      />
      {!externalAllowed(doc) ? (
        <p className={s.small}>This room does not allow messages to outside agents.</p>
      ) : null}
      {problem ? (
        <p className={s.notice} data-code="refused" role="alert">
          <Icon name="warning" size={14} />
          <span>{problem}</span>
        </p>
      ) : null}
    </section>
  );
}

function ListItem({
  doc,
  t,
  me,
  active,
  onPick,
}: {
  doc: RoomDoc;
  t: PeerThread;
  me?: string | null | undefined;
  active: boolean;
  onPick: () => void;
}) {
  const other = otherOf(t, me);
  const i = t.participants.indexOf(other);
  const aid = t.agents[i] ?? `${other}.agent`;
  const state = threadState(t);
  const words: Record<string, string> = {
    replying: "replying",
    capped: "capped",
    paused: "paused",
    stopped: "stopped",
    closed: "closed",
    quiet: "open",
  };
  return (
    <li>
      <button
        type="button"
        className={s.item}
        data-active={active ? "1" : "0"}
        data-state={state}
        aria-current={active ? "true" : undefined}
        style={seat(doc, other)}
        onClick={onPick}
      >
        <span className={s.itemDots} aria-hidden>
          {t.participants.map((p) => (
            <i key={p} style={seat(doc, p)} />
          ))}
        </span>
        <span className={s.itemText}>
          <span className={s.strong}>{agentLabel(doc, aid)}</span>
          <ModelChip label={agentModelLabel(doc, aid)} />
          <span className={s.small}>
            {t.count} message{t.count === 1 ? "" : "s"} · {ago(t.lastTs)}
          </span>
        </span>
        <span className={s.stateTag} data-state={state}>
          {words[state]}
        </span>
      </button>
    </li>
  );
}

/** The Threads panel: how the agents talk to each other, live. Bubbles take the colour of their
 * owner's seat; a thread shows its hop and message counters against the caps, a replying
 * indicator while a delivery is pending, the proposals a peer turn made, and a calm notice when
 * a message could not be delivered, was capped or is paused. */
export function PeerThreads({
  doc,
  me,
  initialThread,
  onOpenLane,
  showSettings = true,
}: {
  doc: RoomDoc;
  me?: string | null | undefined;
  initialThread?: string | undefined;
  onOpenLane?: ((caseId: string, lane: string) => void) | undefined;
  showSettings?: boolean;
}) {
  const threads = peerThreadsOf(doc);
  const [picked, setPicked] = useState<string | null>(initialThread ?? null);
  useEffect(() => {
    if (initialThread) setPicked(initialThread);
  }, [initialThread]);
  const current =
    threads.find((t) => t.id === picked) ?? threads.find((t) => t.status === "open") ?? threads[0];
  const open = threads.filter((t) => t.status === "open").length;
  const title = useId();

  return (
    <section className={s.panel} aria-labelledby={title}>
      <header className={s.panelHead}>
        <h2 id={title} className={s.panelTitle}>
          <Icon name="a2a" size={16} />
          Threads
        </h2>
        <span className={s.count}>
          {open} open{threads.length > open ? ` · ${threads.length - open} closed` : ""}
        </span>
      </header>
      <p className={s.lede}>
        The conductor relays every message; the other agent can only reply and propose.
      </p>
      {me && showSettings ? <PeerSettings doc={doc} me={me} /> : null}
      {current ? (
        <div className={s.split}>
          {threads.length ? (
            <ul className={s.list} aria-label="Threads">
              {threads.map((t) => (
                <ListItem
                  key={t.id}
                  doc={doc}
                  t={t}
                  me={me}
                  active={t.id === current.id}
                  onPick={() => setPicked(t.id)}
                />
              ))}
            </ul>
          ) : null}
          <ThreadView key={current.id} doc={doc} thread={current} me={me} onOpenLane={onOpenLane} />
        </div>
      ) : (
        <div className={s.empty}>
          <Icon name="a2a" size={20} />
          <p className={s.emptyTitle}>No agent has talked yet.</p>
          <p className={s.small}>
            When your agent asks a colleague's agent, the conversation appears here as it happens.
            Each person turns on "Let colleagues' agents ask my agent" first; it is off until then.
          </p>
        </div>
      )}
    </section>
  );
}
