import {
  type CSSProperties,
  type FormEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { Provenance } from "@/shared/a2ui/Provenance";
import type { A2UIAction } from "@/shared/a2ui/Renderer";
import { Button, Icon, seatVar } from "@/shared/ui";
import { agentModelLabel } from "./agentLabel";
import {
  type AgentSession,
  type ApprovalCard,
  type ChatMessage,
  interruptKind,
  isConfirm,
  type JobCard,
  type NoticeCard,
} from "./agentSession";
import {
  buildTurns,
  ChatTrace,
  human,
  TraceTree,
  TreeRow,
  type Turn,
  thinkingLabel,
} from "./ChatTrace";
import s from "./chat.module.css";
import t from "./chat-trace.module.css";
import { nameOf } from "./describe";
import type { JobLive } from "./feed";
import { SurfaceTree } from "./InlineSurface";
import { JobBridgeContext, JobCardView } from "./JobCard";
import {
  AgentPicker,
  ModelChip,
  mentionOf,
  PeerLine,
  PeerReplying,
  roomAgents,
  withResultNames,
} from "./PeerChat";
import { askAgent, type PeerEntry, useMyPeerFeed } from "./peer";
import { Sheet } from "./Sheet";
import { ThinkingLine, ThinkingMark } from "./ThinkingMark";
import type { Interrupt, RoomDoc } from "./types";

/** What a fix action is called on its button. An action not listed here gets a generic label. */
export const FIX_LABELS: Record<string, string> = {
  reconnect: "Reconnect",
  reenter_key: "Re-enter key",
  choose_model: "Choose a model",
  retry: "Try again",
  continue: "Continue",
  open_agent: "Open Your agent",
};

const APPROVAL_LABEL: Record<ApprovalCard["status"], string> = {
  open: "Waiting for you",
  superseded: "Superseded by a newer message",
  approved: "Approved",
  declined: "Declined",
};

function FixButton({
  action,
  onFix,
}: {
  action?: string | undefined;
  onFix?: ((action: string) => void) | undefined;
}) {
  if (!action || !onFix) return null;
  return (
    <button type="button" className={s.fix} onClick={() => onFix(action)}>
      {FIX_LABELS[action] ?? "Open Your agent"}
    </button>
  );
}

type Rec = Record<string, unknown>;

function asRec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

/** One value of a card or a tool's arguments, as a short phrase (never JSON). */
function show(v: unknown): string {
  const text = human(v);
  return text.length > 120 ? `${text.slice(0, 119)}…` : text;
}

/** The name a person knows a capability by: the card's own, else the room document's, else the id. */
function capabilityLabel(card: Rec, doc: RoomDoc | undefined): string {
  if (typeof card.capabilityLabel === "string" && card.capabilityLabel) return card.capabilityLabel;
  const id = typeof card.capability === "string" ? card.capability : "";
  const found = doc?.room.capabilities?.find((c) => c.id === id);
  return found?.label || id;
}

/** The title of a confirm card: "Run Microstructure grain growth · showcase" for a run. */
export function confirmTitle(
  kind: string,
  card: Rec,
  message: string,
  doc: RoomDoc | undefined,
): string {
  if (kind === "confirm_run") {
    const label = capabilityLabel(card, doc);
    const mode = typeof card.mode === "string" ? card.mode : "";
    if (label) return mode ? `Run ${label} · ${mode}` : `Run ${label}`;
  }
  return typeof card.label === "string" && card.label
    ? card.label
    : message || "Needs your confirmation";
}

/** Provider and model of the latest tool call before message `upto`: who asked for the card. */
function attributionBefore(messages: ChatMessage[], upto = messages.length): string {
  for (let i = Math.min(upto, messages.length) - 1; i >= 0; i--) {
    const m = messages[i];
    if (m?.kind === "tool" && (m.provider || m.model))
      return [m.provider, m.model].filter(Boolean).join(" · ");
  }
  return "";
}

const DETAIL_IDS = ["case", "capability", "lane", "target_lane", "to", "scope"];

/** An input value on a card: an uploaded file reads as its name, never as its stored JSON. */
export function inputValue(v: unknown): string {
  let value = v;
  if (typeof value === "string" && value.trim().startsWith("{")) {
    try {
      value = JSON.parse(value);
    } catch {
      // not JSON: shown as written
    }
  }
  const r = asRec(value);
  if (typeof r.artifact === "string" || typeof r.sha256 === "string") {
    const name = [r.name, r.filename, r.label].find((x) => typeof x === "string" && x);
    return typeof name === "string" ? name : "uploaded file";
  }
  return show(value);
}

/** What a confirm card will read or change, from the server-built card. */
function cardRows(card: Rec): { eyebrow: string; rows: [string, string][] } | null {
  if (Array.isArray(card.inputs) && card.inputs.length)
    return {
      eyebrow: "Reads these inputs",
      rows: card.inputs.map((x): [string, string] => {
        const r = asRec(x);
        return [`${show(r.lane)} · ${show(r.field)}`, inputValue(r.value)];
      }),
    };
  if (Array.isArray(card.edits) && card.edits.length)
    return {
      eyebrow: "Changes",
      rows: card.edits.map((x): [string, string] => {
        const r = asRec(x);
        return [show(r.path), show(r.value)];
      }),
    };
  return null;
}

/** The plumbing of a card, kept behind a disclosure: tool, digest (cut, with copy) and ids. */
function CardDetails({ card }: { card: Rec }) {
  const ids = DETAIL_IDS.filter((k) => card[k] !== undefined && card[k] !== null);
  const digest = typeof card.digest === "string" ? card.digest : "";
  if (!card.tool && !digest && !ids.length) return null;
  return (
    <details className={s.details}>
      <summary>Details</summary>
      <dl className={s.facts}>
        {card.tool ? (
          <div>
            <dt>tool</dt>
            <dd className={s.mono}>{show(card.tool)}</dd>
          </div>
        ) : null}
        {digest ? (
          <div>
            <dt>digest</dt>
            <dd>
              <Provenance text={digest} label="digest" />
            </dd>
          </div>
        ) : null}
        {ids.map((k) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className={s.mono}>{show(card[k])}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

/** The body of a confirm card: why it asks, the agent's note (untrusted), what it will read or
 * change, who is behind it, then whatever the caller adds (the buttons) and the details. */
function ConfirmBody({
  card,
  note,
  attribution,
  children,
}: {
  card: Rec;
  note: string;
  attribution: string;
  children?: ReactNode;
}) {
  const rows = cardRows(card);
  const why = typeof card.why === "string" ? card.why : "";
  return (
    <>
      {why ? (
        <p className={s.why}>
          <span className={s.eyebrow}>Why it asks you</span> {why}
        </p>
      ) : null}
      {note ? (
        <p className={s.note}>
          <span className={s.eyebrow}>Agent's note (untrusted)</span> {note}
        </p>
      ) : null}
      {rows ? (
        <div className={s.reads}>
          <span className={s.eyebrow}>{rows.eyebrow}</span>
          <dl className={s.facts}>
            {rows.rows.map(([k, v]) => (
              <div key={`${k}=${v}`}>
                <dt className={s.mono}>{k}</dt>
                <dd className={s.mono}>{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {attribution ? (
        <span className={s.attribution} data-testid="card-attribution">
          {attribution}
        </span>
      ) : null}
      {children}
      <CardDetails card={card} />
    </>
  );
}

export function NoticeCardView({
  card,
  onFix,
}: {
  card: NoticeCard;
  onFix?: ((action: string) => void) | undefined;
}) {
  return (
    <div className={s.notice} role="status">
      <p className={s.msg}>{card.message}</p>
      <FixButton action={card.fixAction} onFix={onFix} />
    </div>
  );
}

/** How a confirm row answers its card: the person's one-click decision on the only open card. */
export type RowAnswer = {
  disabled: boolean;
  dismissed: boolean;
  onApprove: () => void;
  onDecline: () => void;
  onDismiss: () => void;
  onReview: () => void;
};

/** A confirm card as one compact row: a badge, the title, its state and, while it is the only
 * open card, small Approve and Decline buttons. Why it asks, the agent's note, the inputs it reads
 * and the plumbing open under Details. */
export function ApprovalCardView({
  card,
  doc,
  attribution = "",
  answer,
}: {
  card: ApprovalCard;
  doc?: RoomDoc | undefined;
  attribution?: string;
  answer?: RowAnswer | undefined;
}) {
  const [open, setOpen] = useState(false);
  const title = confirmTitle(card.actionKind, card.card, card.message, doc);
  const live = card.status === "open" && answer;
  return (
    <article className={s.row} data-state={card.status} aria-label="Confirm card">
      <div className={s.rowHead}>
        <span className={s.rowBadge}>Approval</span>
        <span className={s.rowTitle} title={title}>
          {title}
        </span>
        <span className={s.chip}>{APPROVAL_LABEL[card.status]}</span>
      </div>
      <div className={s.rowActions}>
        {live && !answer.dismissed ? (
          <>
            <button
              type="button"
              className={s.mini}
              data-tone="primary"
              disabled={answer.disabled}
              onClick={answer.onApprove}
            >
              Approve
            </button>
            <button
              type="button"
              className={s.mini}
              disabled={answer.disabled}
              onClick={answer.onDecline}
            >
              Decline
            </button>
            <button type="button" className={s.link} onClick={answer.onDismiss}>
              Dismiss
            </button>
          </>
        ) : null}
        {live && answer.dismissed ? (
          <button type="button" className={s.link} onClick={answer.onReview}>
            Review
          </button>
        ) : null}
        <button
          type="button"
          className={s.link}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          Details {open ? "▾" : "▸"}
        </button>
      </div>
      {open ? (
        <div className={s.rowDetails}>
          <ConfirmBody card={card.card} note={card.note} attribution={attribution} />
        </div>
      ) : null}
    </article>
  );
}

/** What a surface in the transcript needs to draw and act: the live room (to tell when it is out
 * of date), the person's own actions, and the download of a stored artifact (an image is drawn as
 * an image). */
export type SurfaceHost = {
  doc?: RoomDoc | undefined;
  onAction?: ((a: A2UIAction) => Promise<void>) | undefined;
  loadArtifact?: ((sha: string) => Promise<Blob>) | undefined;
  onOpenStudio?: ((caseId: string) => void) | undefined;
};

function SurfaceView({
  m,
  host,
  openVisual,
}: {
  m: Extract<ChatMessage, { kind: "surface" }>;
  host?: SurfaceHost | undefined;
  openVisual: boolean;
}) {
  return (
    <SurfaceTree
      activity={{ surfaceId: m.surfaceId, messages: m.messages }}
      scope={m.id}
      doc={host?.doc}
      openVisual={openVisual}
      {...(host?.onAction ? { onAction: host.onAction } : {})}
      {...(host?.loadArtifact ? { loadArtifact: host.loadArtifact } : {})}
      {...(host?.onOpenStudio ? { onOpenStudio: host.onOpenStudio } : {})}
    />
  );
}

/** A person's message that asks to see something: then one picture of the turn stays open. */
const ASKS_TO_SEE = /\b(show|display|plot|chart|image|picture|preview|see|view|visuali[sz]e)\b/i;

export function asksToSee(text: string | undefined): boolean {
  return !!text && ASKS_TO_SEE.test(text);
}

const JOB_ID = /(?:\bjob\s+)?\bjob-[0-9a-f]{8,}(?:\s*\(([^)]*)\))?/g;

/** The agent's prose with each job id as a small "run" chip ("grain-growth · showcase"); the id
 * itself stays in the chip's title and in the trace row's details. */
export function withRunChips(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(JOB_ID)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const id = /job-[0-9a-f]+/.exec(match[0])?.[0] ?? "";
    const what = (match[1] ?? "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean)
      .join(" · ");
    out.push(
      <span key={`${id}:${at}`} className={s.runChip} title={id} data-testid="run-chip">
        <Icon name="solvers" size={11} />
        {what || "a run"}
      </span>,
    );
    last = at + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const JOB_TONE: Record<JobCard["status"], "run" | "ok" | "bad" | "neutral"> = {
  queued: "neutral",
  running: "run",
  done: "ok",
  failed: "bad",
  cancelled: "neutral",
};

/** The marker on a turn the room started (``apps/room`` agents/runs.py ``AUTO_PREFIX``). */
export const AUTO_PREFIX = "(Automatic) ";
/** "The run you started for X finished." from the automatic turn's text. */
export function autoNote(text: string): string {
  const first = text.slice(AUTO_PREFIX.length).split(". ")[0] ?? "";
  return `${first.replace("The run you started", "The run your agent started")}. Your agent carries on.`;
}

/** One turn: the person's message, the agent's reply (or the thinking line while none has
 * arrived), the folded trace, then the confirm cards, jobs, surfaces and notices. */
function TurnView({
  turn,
  messages,
  color,
  running,
  thinking,
  onFix,
  host,
  byline,
  answerFor,
}: {
  turn: Turn;
  messages: ChatMessage[];
  color: string;
  running: boolean;
  thinking: boolean;
  onFix?: ((a: string) => void) | undefined;
  host?: SurfaceHost | undefined;
  /** Who answers, and the API and model it runs on. */
  byline?: { name: string; label: string } | undefined;
  /** The one-click answer of a confirm card, when it is the only open one. */
  answerFor?: ((card: ApprovalCard) => RowAnswer | undefined) | undefined;
}) {
  const replies = turn.replies.filter((r) => r.content);
  // One picture of the turn stays open when the person asked to see something: the last one.
  const wantsToSee = asksToSee(turn.user?.content);
  const lastSurface = turn.surfaces[turn.surfaces.length - 1];
  const hasTree = turn.jobs.length > 0;
  return (
    <div className={s.turn} data-testid="turn">
      {turn.user?.content.startsWith(AUTO_PREFIX) ? (
        // The room woke the agent because a run it started settled; nobody typed this.
        <p className={s.auto} data-testid="auto-turn">
          <Icon name="replay" size={13} />
          {autoNote(turn.user.content)}
        </p>
      ) : turn.user ? (
        <div className={s.me} style={{ background: color }}>
          {turn.user.content}
        </div>
      ) : null}
      {byline?.label && replies.length ? (
        <div className={s.byline} data-testid="reply-byline">
          <span className={s.bylineName}>{byline.name}</span>
          <ModelChip label={byline.label} />
        </div>
      ) : null}
      {replies.map((r, i) => (
        <div key={r.id} className={t.reply} data-testid="agent-reply">
          {i === 0 ? (
            <span className={t.gutter}>
              <ThinkingMark size={18} />
            </span>
          ) : null}
          <p className={i === 0 ? t.prose : `${t.prose} ${t.proseOnly}`}>
            {withRunChips(r.content).flatMap((part) =>
              typeof part === "string" ? withResultNames(part, host?.doc) : [part],
            )}
          </p>
        </div>
      ))}
      {thinking ? <ThinkingLine label={thinkingLabel(turn.steps)} /> : null}
      {turn.steps.length ? (
        <div className={t.indent}>
          <ChatTrace
            steps={turn.steps}
            approvals={turn.approvals}
            running={running}
            fix={(card) => <FixButton action={card.fixAction} onFix={onFix} />}
          />
        </div>
      ) : null}
      {turn.approvals.map((a) => (
        <div key={a.id} className={t.indent}>
          <ApprovalCardView
            card={a}
            doc={host?.doc}
            attribution={attributionBefore(messages, messages.indexOf(a))}
            answer={answerFor?.(a)}
          />
        </div>
      ))}
      {hasTree ? (
        <div className={t.indent}>
          <TraceTree label="Runs">
            {turn.jobs.map((j) => (
              <TreeRow
                key={j.id}
                kind="Run"
                name={j.title || "Run"}
                summary={j.message}
                status={
                  j.status === "running" && typeof j.progress === "number"
                    ? `running ${Math.round(j.progress * 100)}%`
                    : j.status
                }
                tone={JOB_TONE[j.status]}
                attention={j.status === "failed"}
                testId="job-row"
              >
                <JobCardView card={j} onFix={onFix} />
              </TreeRow>
            ))}
          </TraceTree>
        </div>
      ) : null}
      {turn.surfaces.map((m) => (
        <div key={m.id} className={t.indent}>
          <SurfaceView m={m} host={host} openVisual={wantsToSee && m === lastSurface} />
        </div>
      ))}
      {turn.notices.map((n) => (
        <div key={n.id} className={t.indent}>
          <NoticeCardView card={n} onFix={onFix} />
        </div>
      ))}
    </div>
  );
}

type Pick = boolean | "later";

type Decision = { approved: boolean; reason?: string };

/** Whether a confirm card in the transcript is the card of an open interrupt. */
function cardOf(card: ApprovalCard, i: Interrupt): boolean {
  const meta = asRec(i.metadata);
  return card.id === `approval-${i.id}` || card.actionId === (meta.actionId ?? i.id);
}

/** Several open approvals, or a proposal (it needs a reason), are answered here together. The
 * only open confirm card is answered on its own row in the transcript instead. */
function ApprovalPanel({
  session,
  disabled,
  doc,
  attribution,
  inRow,
}: {
  session: AgentSession;
  disabled: boolean;
  doc: RoomDoc | undefined;
  attribution: string;
  /** The only open card is answered on its row: nothing to show here. */
  inRow: boolean;
}) {
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [pick, setPick] = useState<Record<string, Pick>>({});
  const [hidden, setHidden] = useState<string[]>([]);
  const open = session.interrupts;
  if (!open.length || inRow) return null;

  // One open confirm card is answered by its own button. Several (or a proposal, which needs a
  // reason) are batched under "Send my decisions".
  const first = open[0];
  const only = open.length === 1 && first && isConfirm(first) ? first : null;
  if (only && hidden.includes(only.id)) {
    return (
      <section className={s.approvalBar} aria-label="Waiting for your decision">
        <span className={s.small}>Waiting for your decision · 1</span>
        <button type="button" className={s.fix} onClick={() => setHidden([])}>
          Review
        </button>
      </section>
    );
  }

  const needsReason = (i: Interrupt) => !isConfirm(i);
  const ready = open.every((i) => {
    const p = pick[i.id];
    if (p === "later") return true;
    if (typeof p !== "boolean") return false;
    return !needsReason(i) || (reasons[i.id] ?? "").trim().length > 0;
  });

  return (
    <section className={s.approvals} aria-label="Waiting for your decision">
      <h3 className={s.h3}>Waiting for your decision · {open.length}</h3>
      {open.map((i) => {
        const meta = (i.metadata ?? {}) as unknown as Rec;
        const confirm = isConfirm(i);
        const card = asRec(meta.card);
        const note = typeof meta.note === "string" ? meta.note : "";
        const caused = asRec(meta.causedBy);
        const labels: [Pick, string][] = [
          [true, confirm ? "Approve" : "Accept"],
          [false, "Decline"],
          ["later", confirm ? "Dismiss" : "Later"],
        ];
        const alone = only !== null && i.id === only.id;
        const choose = (v: Pick) => {
          if (alone) {
            if (v === "later") setHidden([i.id]);
            else void session.answer({ [i.id]: { approved: v } });
            return;
          }
          setPick((p) => ({ ...p, [i.id]: v }));
        };
        const buttons = (
          <div className={s.choices}>
            {labels.map(([v, label]) => (
              <button
                key={label}
                type="button"
                className={s.choice}
                aria-pressed={alone ? undefined : pick[i.id] === v}
                disabled={alone && disabled && v !== "later"}
                onClick={() => choose(v)}
              >
                {label}
              </button>
            ))}
          </div>
        );
        return (
          <div key={i.id} className={s.approval} data-kind={interruptKind(i)}>
            <p className={s.msg}>
              {confirm ? confirmTitle(interruptKind(i), card, i.message, doc) : i.message}
            </p>
            {!confirm && caused.msg ? (
              <p className={s.small} data-testid="caused-by">
                Caused by a message from {show(caused.from || "a peer agent")} in thread{" "}
                <span className={s.mono}>{show(caused.thread)}</span>
              </p>
            ) : null}
            {buttons}
            {confirm ? (
              <details className={s.details}>
                <summary>Details</summary>
                <ConfirmBody card={card} note={note} attribution={attribution} />
              </details>
            ) : null}
            {!alone && typeof pick[i.id] === "boolean" ? (
              <label className={s.reason}>
                <span className="visually-hidden">Reason</span>
                <input
                  value={reasons[i.id] ?? ""}
                  placeholder={
                    confirm ? "A reason (optional)" : "Your reason (it becomes a lesson)"
                  }
                  onChange={(e) => setReasons((r) => ({ ...r, [i.id]: e.target.value }))}
                />
              </label>
            ) : null}
          </div>
        );
      })}
      {only ? (
        <p className={s.small}>Dismiss keeps it open.</p>
      ) : (
        <>
          <Button
            disabled={!ready || disabled}
            onClick={() =>
              session.answer(
                Object.fromEntries(
                  open.map((i): [string, Decision | "later"] => {
                    const p = pick[i.id];
                    if (p === "later" || p === undefined) return [i.id, "later"];
                    const reason = (reasons[i.id] ?? "").trim();
                    return [i.id, reason ? { approved: p, reason } : { approved: p }];
                  }),
                ),
              )
            }
          >
            Send my decisions
          </Button>
          <p className={s.small}>Every open approval needs an answer; Dismiss keeps it open.</p>
        </>
      )}
    </section>
  );
}

/** Talk to your personal agent over its AG-UI session, and answer its approvals there. The
 * session lives above the sheet (RoomPage), so the transcript stays when the sheet closes. The
 * agent reads, labels and negotiates; numbers come only from solvers, never from this chat. */
export function AgentChat({
  session: a,
  person,
  agentName,
  onFix,
  jobLive,
  surfaces,
  onClose,
}: {
  session: AgentSession;
  person: { id: string; name: string; color: string };
  agentName: string;
  /** The peer-thread strip (WS-P). The bubbles and the pop-up show threads now; not drawn. */
  peerStrip?: ReactNode | undefined;
  onFix?: ((action: string) => void) | undefined;
  /** The live view of a job from the room feed (queue entry plus the run's progress). */
  jobLive?: ((jobId: string) => JobLive | undefined) | undefined;
  /** What the agent's surfaces in the transcript draw and act with. */
  surfaces?: SurfaceHost | undefined;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const c = seatVar(person.color);
  const doc = surfaces?.doc;

  // One chat for every agent in the room: your own answers here as before; a colleague's agent
  // gets your words as an agent-to-agent message and answers in the same chat, in its colour.
  const agents = roomAgents(doc, person.id);
  const own = `${person.id}.agent`;
  const [target, setTarget] = useState(own);
  const other = agents.find((x) => x.aid === target && !x.mine) ?? null;
  const feed = useMyPeerFeed(doc, person.id);
  const [asking, setAsking] = useState(false);
  const [peerNote, setPeerNote] = useState<string | null>(null);
  const myLabel = agentModelLabel(doc, person.id);

  const turns = buildTurns(a.messages);
  // Turns and agent-to-agent messages in the order they happened. A turn's time is its stored
  // time after a reload, else when this chat first saw it.
  const seen = useRef(new Map<string, number>());
  const timeOf = (turn: Turn, i: number): number => {
    let at = seen.current.get(turn.id);
    if (at === undefined) {
      at = turn.user?.at ?? Date.now() / 1000;
      seen.current.set(turn.id, at);
    }
    return at + i * 1e-6;
  };
  type Item =
    | { k: "turn"; at: number; turn: Turn; i: number }
    | { k: "peer"; at: number; m: PeerEntry };
  const items: Item[] = [
    ...turns.map((turn, i): Item => ({ k: "turn", at: timeOf(turn, i), turn, i })),
    ...feed.entries.map((m): Item => ({ k: "peer", at: m.ts, m })),
  ].sort((x, y) => x.at - y.at);
  const last = a.messages[a.messages.length - 1];
  // Working, and no text of this reply has arrived yet.
  const thinking =
    a.running && !(last?.kind === "text" && last.role === "assistant" && last.content);

  const count = a.messages.length + feed.entries.length + feed.waiting.length;
  useEffect(() => {
    if (count) end.current?.scrollIntoView?.({ block: "end" });
  }, [count]);

  // Only a proposal blocks a new message. A confirm card is superseded by sending one.
  const blocking = a.interrupts.some((i) => !isConfirm(i));

  // The only open confirm card is answered on its own row, with one click.
  const firstOpen = a.interrupts[0];
  const sole = a.interrupts.length === 1 && firstOpen && isConfirm(firstOpen) ? firstOpen : null;
  const soleInRow =
    !!sole &&
    a.messages.some((m) => m.kind === "approval" && m.status === "open" && cardOf(m, sole));
  const [dismissed, setDismissed] = useState<string | null>(null);
  const answerFor = (card: ApprovalCard): RowAnswer | undefined =>
    sole && soleInRow && cardOf(card, sole)
      ? {
          disabled: a.running,
          dismissed: dismissed === sole.id,
          onApprove: () => void a.answer({ [sole.id]: { approved: true } }),
          onDecline: () => void a.answer({ [sole.id]: { approved: false } }),
          onDismiss: () => setDismissed(sole.id),
          onReview: () => setDismissed(null),
        }
      : undefined;

  function change(value: string) {
    // "@ankita " picks Ankita's agent and leaves the rest of the message.
    const m = mentionOf(value, agents, doc);
    if (m) {
      setTarget(m.agent.aid);
      setText(m.rest ? `${m.rest} ` : "");
      return;
    }
    setText(value);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) return;
    if (other && doc) {
      if (asking) return;
      setAsking(true);
      setPeerNote(null);
      void askAgent(doc.room.id, other.person, t)
        .then((r) => {
          if (r.sent) setText("");
          else setPeerNote(r.text || "The message was not sent.");
        })
        .catch((err: unknown) =>
          setPeerNote(err instanceof Error ? err.message : "The message was not sent."),
        )
        .finally(() => setAsking(false));
      return;
    }
    if (a.running || blocking) return;
    setText("");
    void a.send(t);
  }
  const busy = other ? asking : a.running || blocking;

  return (
    <Sheet
      title={agentName}
      sub={myLabel ? <ModelChip label={myLabel} /> : undefined}
      icon={<Icon name="agent" size={18} accent={c} />}
      narrow
      docked
      onClose={onClose}
    >
      {/* The log is the only scroller; the panel above and the picker and composer below stay
          put, so nothing ever renders under the composer. */}
      <div className={s.wrap} style={{ "--accent": c } as CSSProperties}>
        <ApprovalPanel
          session={a}
          disabled={a.running}
          doc={surfaces?.doc}
          attribution={attributionBefore(a.messages)}
          inRow={soleInRow}
        />

        <div className={s.log} aria-live="polite">
          {a.messages.length || feed.entries.length ? (
            <JobBridgeContext.Provider
              value={{
                live: jobLive,
                send: (text) => {
                  if (!a.running) void a.send(text);
                },
              }}
            >
              {items.map((item) =>
                item.k === "turn" ? (
                  <TurnView
                    key={item.turn.id}
                    turn={item.turn}
                    messages={a.messages}
                    color={c}
                    running={a.running}
                    thinking={thinking && item.i === turns.length - 1}
                    onFix={onFix}
                    host={surfaces}
                    byline={{ name: agentName, label: myLabel }}
                    answerFor={answerFor}
                  />
                ) : (
                  <PeerLine key={item.m.id} doc={doc} m={item.m} me={person.id} />
                ),
              )}
              {feed.waiting.map((w) => (
                <PeerReplying key={w.thread} doc={doc} agent={w.agent} />
              ))}
            </JobBridgeContext.Provider>
          ) : (
            <p className={s.small}>
              {a.running
                ? "Connecting to your agent…"
                : "Ask what it is watching, why it proposed something, or what to try next."}
            </p>
          )}
          <div ref={end} className={s.end} />
        </div>

        <div className={s.foot}>
          {a.error ? (
            <p className={s.bad} role="alert">
              {a.error}
            </p>
          ) : null}

          {peerNote ? (
            <p className={s.small} role="status" data-testid="peer-note">
              {peerNote}
            </p>
          ) : null}

          <AgentPicker
            agents={agents}
            target={other ? other.aid : own}
            onPick={(aid) => {
              setTarget(aid);
              setPeerNote(null);
            }}
          />
          <form className={s.composer} onSubmit={submit}>
            <label className="visually-hidden" htmlFor="agent-say">
              {other ? `Message ${other.name}` : "Message your agent"}
            </label>
            <input
              id="agent-say"
              value={text}
              maxLength={2000}
              placeholder={
                other
                  ? `Message ${other.name}; it answers for ${nameOf(other.person, doc ?? null)}`
                  : blocking
                    ? "Answer the approvals first"
                    : `Message ${agentName}`
              }
              disabled={!other && blocking}
              onChange={(e) => change(e.target.value)}
            />
            <Button type="submit" small disabled={!text.trim() || busy}>
              Send
            </Button>
          </form>
        </div>
      </div>
    </Sheet>
  );
}
