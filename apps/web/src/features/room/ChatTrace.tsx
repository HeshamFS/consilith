import { type ReactNode, useEffect, useState } from "react";
import type {
  ApprovalCard,
  ChatMessage,
  JobCard,
  NoticeCard,
  SurfaceCard,
  TextMessage,
  ToolCard,
} from "./agentSession";
import s from "./chat-trace.module.css";

type Rec = Record<string, unknown>;

function asRec(v: unknown): Rec {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Rec) : {};
}

/** A value as a short phrase a person can read: never braces, quotes or a JSON dump. */
export function human(v: unknown, depth = 0): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) {
    const items = v.slice(0, 5).map((x) => human(x, depth + 1));
    return items.join(", ") + (v.length > 5 ? ` +${v.length - 5} more` : "");
  }
  const entries = Object.entries(asRec(v));
  if (depth >= 1) return `${entries.length} field${entries.length === 1 ? "" : "s"}`;
  return entries
    .slice(0, 4)
    .map(([k, x]) => `${k}: ${human(x, depth + 1)}`)
    .join("; ");
}

/** What kind of step a tool call is. */
export type StepKind =
  | "skill"
  | "knowledge"
  | "catalog"
  | "mcp"
  | "platform"
  | "run"
  | "ui"
  | "approval"
  | "peer";

const KIND_LABEL: Record<StepKind, string> = {
  skill: "Skill",
  knowledge: "Knowledge",
  catalog: "Catalog",
  mcp: "MCP",
  platform: "Platform",
  run: "Run",
  ui: "Gen UI",
  approval: "Approval",
  peer: "Peer",
};

export function toolKind(tool: string): StepKind {
  if (tool === "skill.read") return "skill";
  if (tool === "kb.read") return "knowledge";
  if (tool.startsWith("catalog.")) return "catalog";
  if (tool === "run.start" || tool === "run.cancel" || tool.startsWith("job.")) return "run";
  if (tool.startsWith("ui.")) return "ui";
  if (tool.startsWith("peer.")) return "peer";
  if (tool.startsWith("mcp:") || tool.startsWith("mcp.") || tool.startsWith("plugin:"))
    return "mcp";
  return "platform";
}

/** One turn of the chat: the person's message, the agent's reply, then everything the agent did
 * for it. Built from the flat transcript, so the stored history and a live run group the same. */
export type Turn = {
  id: string;
  user: TextMessage | null;
  replies: TextMessage[];
  steps: ToolCard[];
  approvals: ApprovalCard[];
  jobs: JobCard[];
  surfaces: SurfaceCard[];
  notices: NoticeCard[];
};

export function buildTurns(messages: ChatMessage[]): Turn[] {
  const turns: Turn[] = [];
  let cur: Turn | null = null;
  const fresh = (id: string, user: TextMessage | null): Turn => ({
    id,
    user,
    replies: [],
    steps: [],
    approvals: [],
    jobs: [],
    surfaces: [],
    notices: [],
  });
  for (const m of messages) {
    if (m.kind === "text" && m.role === "user") {
      cur = fresh(m.id, m);
      turns.push(cur);
      continue;
    }
    if (!cur) {
      cur = fresh("turn-start", null);
      turns.push(cur);
    }
    if (m.kind === "text") cur.replies.push(m);
    else if (m.kind === "tool") cur.steps.push(m);
    else if (m.kind === "approval") cur.approvals.push(m);
    else if (m.kind === "job") cur.jobs.push(m);
    else if (m.kind === "surface") cur.surfaces.push(m);
    else cur.notices.push(m);
  }
  return turns;
}

type Tone = "run" | "ok" | "bad" | "wait" | "neutral";

export type StepStatus = { tone: Tone; label: string };

const WAITING: StepStatus = { tone: "wait", label: "Awaiting your approval" };
const NOT_RUN: StepStatus = { tone: "neutral", label: "Not run" };

/** The status of a step, in words. A step that waits for an approval follows the confirm card
 * of its turn: waiting while it is open, then Approved or Declined (grey, never red). */
export function stepStatus(
  card: ToolCard,
  approvals: ApprovalCard[],
  running: boolean,
): StepStatus {
  switch (card.state) {
    case "running":
      return { tone: "run", label: "Running" };
    case "ok":
      return { tone: "ok", label: "Done" };
    case "error":
      return { tone: "bad", label: "Failed" };
    case "not_executed":
      return NOT_RUN;
    case "cancelled":
      return { tone: "neutral", label: "Cancelled" };
    case "awaiting_approval": {
      if (approvals.some((a) => a.status === "open")) return WAITING;
      if (approvals.some((a) => a.status === "declined"))
        return { tone: "neutral", label: "Declined" };
      if (approvals.some((a) => a.status === "approved")) return { tone: "ok", label: "Approved" };
      if (approvals.length) return NOT_RUN;
      return running ? WAITING : NOT_RUN;
    }
  }
}

/** The steps to show: a call that waited for an approval is dropped once the same tool ran
 * afterwards, so an approved run is one step, not two. */
export function visibleSteps(steps: ToolCard[]): ToolCard[] {
  return steps.filter(
    (c, i) =>
      c.state !== "awaiting_approval" ||
      !steps.slice(i + 1).some((n) => n.tool === c.tool && n.state !== "awaiting_approval"),
  );
}

function capabilityOf(card: ToolCard): string {
  const stored = asRec(card.args.card);
  const id = card.args.capability ?? stored.capabilityLabel ?? stored.capability;
  return typeof id === "string" ? id : "";
}

function lower(text: string): string {
  return text ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

function mcpName(tool: string): string {
  return tool.replace(/^(mcp|plugin)[:.]/, "");
}

/** "read skill", "checked catalog", "started grain growth": what a finished step did. */
export function stepPhrase(c: ToolCard): string {
  const kind = toolKind(c.tool);
  if (kind === "skill") return "read skill";
  if (kind === "knowledge") return "read knowledge";
  if (kind === "catalog") return "checked catalog";
  if (c.tool === "run.start") return `started ${capabilityOf(c) || "a run"}`;
  if (c.tool === "run.cancel") return "cancelled a run";
  if (kind === "run") return c.tool.startsWith("job.") ? "checked a job" : "checked a run";
  if (kind === "ui") return "showed a view";
  if (kind === "peer") return "messaged a peer";
  if (kind === "mcp") return `used ${mcpName(c.tool)}`;
  return c.label && c.label !== c.tool ? lower(c.label) : c.tool.replace(/\./g, " ");
}

/** "Reading the skill…": what the agent is doing, from its latest step. */
export function stepProgress(c: ToolCard): string {
  const kind = toolKind(c.tool);
  if (kind === "skill") return "Reading the skill…";
  if (kind === "knowledge") return "Reading the knowledge base…";
  if (kind === "catalog") return "Checking the catalog…";
  if (c.tool === "run.start") return `Starting ${capabilityOf(c) || "a run"}…`;
  if (c.tool === "run.cancel") return "Cancelling the run…";
  if (kind === "run") return c.tool.startsWith("job.") ? "Checking the job…" : "Checking the run…";
  if (kind === "ui") return "Preparing a view…";
  if (kind === "peer") return "Messaging a peer…";
  if (kind === "mcp") return `Using ${mcpName(c.tool)}…`;
  return c.label && c.label !== c.tool ? `${c.label}…` : "Working…";
}

/** The label of the thinking line: the step in flight, else a plain "Thinking…". */
export function thinkingLabel(steps: ToolCard[]): string {
  const last = steps[steps.length - 1];
  return last && last.state === "running" ? stepProgress(last) : "Thinking…";
}

function seconds(ms: number): string {
  const sec = ms / 1000;
  return sec < 10 ? `${sec.toFixed(1)} s` : `${Math.round(sec)} s`;
}

function duration(c: ToolCard): string {
  if (c.startedAt === undefined || c.endedAt === undefined) return "";
  const ms = c.endedAt - c.startedAt;
  return ms >= 100 ? seconds(ms) : "";
}

function totalTime(steps: ToolCard[]): string {
  const timed = steps.filter((c) => c.startedAt !== undefined && c.endedAt !== undefined);
  if (!timed.length) return "";
  const from = Math.min(...timed.map((c) => c.startedAt ?? 0));
  const to = Math.max(...timed.map((c) => c.endedAt ?? 0));
  return to - from >= 100 ? seconds(to - from) : "";
}

/** The ids of a result and a job id in a sentence are details of the expanded trace only. */
function plain(summary: string, ids: string[]): string {
  let out = summary;
  for (const id of ids) out = out.split(id).join("");
  return out
    .replace(/\bjob[-_][\w-]+/gi, "")
    .replace(/\(\s*\)/g, "")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;])/g, "$1")
    .trim();
}

/** The folded line: "Read skill · checked catalog · started grain growth · 4 steps · 12 s". */
export function traceSummary(steps: ToolCard[]): string {
  const phrases: string[] = [];
  for (const c of steps) {
    const p = stepPhrase(c);
    if (phrases[phrases.length - 1] !== p) phrases.push(p);
  }
  const shown = phrases.slice(0, 3);
  const text = shown.join(" · ") + (phrases.length > 3 ? ` · +${phrases.length - 3}` : "");
  const head = text.charAt(0).toUpperCase() + text.slice(1);
  const time = totalTime(steps);
  return [head, `${steps.length} ${steps.length === 1 ? "step" : "steps"}`, time]
    .filter(Boolean)
    .join(" · ");
}

const DETAIL_IDS = ["case", "capability", "mode", "lane", "target_lane", "to", "scope"];

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={s.chev} data-open={open} width="10" height="10" viewBox="0 0 10 10" aria-hidden>
      <path d="M2.5 3.5 5 6.5 7.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function StepRow({
  card,
  status,
  fix,
}: {
  card: ToolCard;
  status: StepStatus;
  fix?: ((card: ToolCard) => ReactNode) | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [raw, setRaw] = useState(false);
  const stored = asRec(card.args.card);
  const approved = typeof stored.kind === "string" && stored.kind.startsWith("confirm_");
  const args = Object.entries(card.args).filter(([k]) => !(approved && k === "card"));
  const storedRows = approved
    ? DETAIL_IDS.filter((k) => stored[k] !== undefined && stored[k] !== null).map(
        (k): [string, string] => [k, human(stored[k])],
      )
    : [];
  const argRows = args.map(([k, v]): [string, string] => [k, human(v)]);
  const rows = [...storedRows.filter(([k]) => !argRows.some(([a]) => a === k)), ...argRows];
  const kind = status.tone === "wait" ? "approval" : toolKind(card.tool);
  const line = plain(card.summary, card.resultIds);
  const time = duration(card);
  const detail = `${card.id}-detail`;
  const full = approved
    ? `Approved by you · ${card.summary || status.label.toLowerCase()}`
    : card.summary;
  const rawText = JSON.stringify(
    {
      tool: card.tool,
      args: card.args,
      result: { status: card.state, summary: card.summary, ids: card.resultIds },
    },
    null,
    2,
  );
  return (
    <li className={s.step} data-tone={status.tone} data-state={card.state} data-kind={kind}>
      <span className={s.dot} aria-hidden />
      <button
        type="button"
        className={s.stepHead}
        aria-expanded={open}
        aria-controls={detail}
        aria-label={`${card.label}: ${status.label}`}
        onClick={() => setOpen((v) => !v)}
      >
        <Chevron open={open} />
        <span className={s.badge} data-testid="step-kind">
          {KIND_LABEL[kind]}
        </span>
        <span className={s.stepName}>{card.tool}</span>
        <span className={s.stepSummary}>{approved ? `Approved by you · ${line}` : line}</span>
        <span className={s.stepStatus} data-testid="step-status">
          {status.label}
        </span>
        {time ? <span className={s.stepTime}>{time}</span> : null}
      </button>
      <div className={s.expand} data-open={open} id={detail}>
        <div className={s.expandInner} aria-hidden={!open}>
          <div className={s.detail}>
            {raw ? (
              <pre className={s.pre}>{rawText}</pre>
            ) : (
              <>
                {rows.length ? (
                  <section>
                    <span className={s.defHead}>Arguments</span>
                    <dl className={s.defs}>
                      {rows.map(([k, v]) => (
                        <div key={k}>
                          <dt>{k}</dt>
                          <dd>{v}</dd>
                        </div>
                      ))}
                    </dl>
                  </section>
                ) : null}
                <section>
                  <span className={s.defHead}>Result</span>
                  <dl className={s.defs}>
                    <div>
                      <dt>status</dt>
                      <dd>{status.label}</dd>
                    </div>
                    {full ? (
                      <div>
                        <dt>summary</dt>
                        <dd>{full}</dd>
                      </div>
                    ) : null}
                    {card.resultIds.length ? (
                      <div>
                        <dt>ids</dt>
                        <dd className={s.mono}>{card.resultIds.join(", ")}</dd>
                      </div>
                    ) : null}
                    {card.provider || card.model ? (
                      <div>
                        <dt>model</dt>
                        <dd>{[card.provider, card.model].filter(Boolean).join(" · ")}</dd>
                      </div>
                    ) : null}
                  </dl>
                </section>
              </>
            )}
            <button type="button" className={s.rawToggle} onClick={() => setRaw((v) => !v)}>
              {raw ? "Formatted" : "Raw"}
            </button>
          </div>
        </div>
      </div>
      {card.state === "error" && fix ? fix(card) : null}
    </li>
  );
}

/** One collapsed row of the turn's tree: a result, a view, a job or a notice the agent produced.
 * The row is one line (kind, name, summary, status); its content renders only when opened. It
 * opens by itself on an error or a pending approval. */
export function TreeRow({
  kind,
  name,
  summary = "",
  status = "",
  tone = "neutral",
  attention = false,
  testId,
  children,
}: {
  kind: string;
  name: string;
  summary?: string;
  status?: string;
  tone?: Tone;
  attention?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(attention);
  useEffect(() => {
    if (attention) setOpen(true);
  }, [attention]);
  return (
    <li className={s.step} data-tone={tone} data-testid={testId}>
      <span className={s.dot} aria-hidden />
      <button
        type="button"
        className={s.stepHead}
        aria-expanded={open}
        aria-label={status ? `${name}: ${status}` : name}
        onClick={() => setOpen((v) => !v)}
      >
        <Chevron open={open} />
        <span className={s.badge}>{kind}</span>
        <span className={s.stepName}>{name}</span>
        {summary ? <span className={s.stepSummary}>{summary}</span> : null}
        {status ? (
          <span className={s.stepStatus} data-testid="row-status">
            {status}
          </span>
        ) : null}
      </button>
      {open ? <div className={s.treeBody}>{children}</div> : null}
    </li>
  );
}

/** The rows of a turn, on the same rail as the trace. */
export function TraceTree({ label, children }: { label: string; children: ReactNode }) {
  return (
    <ol className={`${s.rail} ${s.tree}`} aria-label={label}>
      {children}
    </ol>
  );
}

/** A one-line row with no content to open, such as "out of date" on a view. */
export function TreeNote({ children }: { children: ReactNode }) {
  return (
    <li className={s.step} data-tone="neutral">
      <span className={s.dot} aria-hidden />
      <p className={s.treeNote}>{children}</p>
    </li>
  );
}

/** The agent's work for one reply, folded into one line. It opens by itself when a step failed
 * or an approval is pending, and stays open once opened. */
export function ChatTrace({
  steps,
  approvals,
  running,
  fix,
}: {
  steps: ToolCard[];
  approvals: ApprovalCard[];
  running: boolean;
  fix?: ((card: ToolCard) => ReactNode) | undefined;
}) {
  const shown = visibleSteps(steps);
  const statuses = shown.map((c) => stepStatus(c, approvals, running));
  const attention = statuses.some((st) => st.tone === "bad" || st.tone === "wait");
  const [open, setOpen] = useState(attention);
  useEffect(() => {
    if (attention) setOpen(true);
  }, [attention]);
  if (!shown.length) return null;
  const failed = statuses.some((st) => st.tone === "bad");
  const pending = statuses.some((st) => st.tone === "wait");
  return (
    <section className={s.trace} aria-label="Agent trace" data-open={open}>
      <button
        type="button"
        className={s.summary}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={s.summaryText}>{traceSummary(shown)}</span>
        {failed ? <span className={s.flagBad}>failed</span> : null}
        {pending ? <span className={s.flagWait}>awaiting approval</span> : null}
        <Chevron open={open} />
      </button>
      {open ? (
        <ol className={s.rail}>
          {shown.map((c, i) => (
            <StepRow key={c.id} card={c} status={statuses[i] as StepStatus} fix={fix} />
          ))}
        </ol>
      ) : null}
    </section>
  );
}
