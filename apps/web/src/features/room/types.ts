/** Wire types of the room server, from apps/room (room.py, document.py, agui.py, a2ui.py).
 * Contract records are camelCase with absent fields omitted. */

import type { PatchOp } from "@/shared/agui/patch";
import type { Capability, PluginDescriptor } from "@/shared/domain/disciplines";

export type CaseId = string;
export type StampKind = "verified" | "checking" | "warning" | "blocked";
export type Autonomy = "observe" | "suggest" | "edit_my_lanes" | "run_solvers";
export type AgentStatus = "idle" | "thinking" | "working" | "delegating" | "waiting" | "paused";
export type Protocol = "AG-UI" | "A2UI" | "A2A" | "MCP" | "ROOM";

export type Num = { v: number; unit: string };

export interface Person {
  id: string;
  name: string;
  color: string;
  stageColor?: string;
  colorName?: string;
  role: string;
  roleLabel?: string;
}

export interface Watch {
  id: string;
  case: string;
  quantity: string;
  op: ">=" | "<=" | ">" | "<" | "abs<=";
  threshold: number;
  unit: string;
  onBreak: "warn" | "propose";
  label: string;
  reference?: string;
}

export interface AgentConfig {
  skills: string[];
  toolsAllowed: string[];
  toolsDenied: string[];
  autonomy: Autonomy;
  watches: Watch[];
  goal: string;
  computeBudgetS: number;
  /** A provider id from the server's registry (`GET /providers`), not a closed set here. */
  modelProvider: string;
  modelName?: string;
}

export interface BroughtCard {
  name: string;
  description: string;
  version: string;
  skills: string[];
  interfaces: { binding: string; version: string; url: string }[];
}

export interface PersonalAgent {
  id: string;
  kind: "personal";
  owner: string;
  status: AgentStatus;
  statusDetail: string;
  brought: boolean;
  cardUrl: string | null;
  card?: BroughtCard;
  config: AgentConfig;
}

export interface RoomAgent {
  id: string;
  kind: "room";
  owner: null;
  status: AgentStatus;
  statusDetail: string;
}

export type Agent = PersonalAgent | RoomAgent;

export interface Proposal {
  id: string;
  author: string;
  onBehalfOf: string;
  case: CaseId;
  targetLane: string;
  laneOwner?: string | null;
  patch: PatchOp[];
  reason: string;
  evidence: string[];
  status: "awaiting-owner" | "accepted" | "declined" | "withdrawn" | "failed";
  interruptId?: string;
  createdSeq: number;
}

export interface WarningFlag {
  id: string;
  author: string;
  to: string;
  case: string;
  lane: string;
  message: string;
  evidence: string[];
  acknowledged: boolean;
}

export interface ResultRecord {
  id: string;
  case: CaseId;
  runId: string;
  quantity: string;
  kind?: import("@/shared/a2ui/ResultValue").ResultKind;
  value?: unknown;
  artifacts?: { sha256: string; mediaType: string; role?: string }[];
  unit: string;
  tier: 0 | 1 | 2;
  stamp: StampKind;
  status: "current" | "stale" | "superseded" | "failed" | "timeout";
  illustrative?: boolean;
  provenance: { tool: string; solverVersion: string; wallTimeS: number; model?: string };
  validity?: { range: string; inside: boolean; reason: string };
  ledgerId?: string;
}

export interface Run {
  id: string;
  case: CaseId;
  tool: string;
  tier: 0 | 1 | 2;
  status: "running" | "done" | "late" | "failed" | "timeout";
  error?: string;
  requestedBy: string;
  started: number;
}

export interface Lesson {
  id: string;
  scope: "personal" | "room" | "case";
  holder: string;
  text: string;
  taughtBy: string;
  reason: string;
  ts: number;
  deleted: boolean;
}

export interface RoomDoc {
  room: {
    id: string;
    title: string;
    people: Record<string, Person>;
    agents: Record<string, Agent>;
    activeCase: CaseId;
    capabilities?: Capability[];
    plugins?: PluginDescriptor[];
    activePlugins?: string[];
  };
  lanes: Partial<Record<CaseId, Record<string, string | null>>>;
  twin: Partial<Record<CaseId, Record<string, Record<string, unknown>>>>;
  results: Record<string, ResultRecord>;
  runs: Record<string, Run>;
  proposals: Record<string, Proposal>;
  decisions: Record<
    string,
    { id: string; proposal: string; by: string; accept: boolean; reason: string; seq: number }
  >;
  warnings: Record<string, WarningFlag>;
  tensions: Record<string, { id: string; case: CaseId; status: string }>;
  ledger: Record<
    string,
    {
      id: string;
      ts: number;
      current?: boolean;
      subject: import("@/shared/a2ui/ResultValue").TypedResult & {
        quantity: string;
        stamp: StampKind;
        method: string;
      };
    }
  >;
  memory: { room: Record<string, Lesson>; case: Record<string, Lesson> };
  queue: Record<string, { id: string; status: string; case: string; why: string }>;
}

export interface LogEvent {
  seq: number;
  ts: number;
  kind: string;
  protocol: Protocol;
  actor: string;
  meta: Record<string, unknown>;
}

export interface WiringEvent {
  journalSeq: number;
  protocol: Protocol;
  kind: string;
  sender: string;
  receiver: string;
  refs: Record<string, string>;
}

export interface Presence {
  /** A person id, or an agent id (`<owner>.agent`) when `agent` is set. */
  person: string;
  ts: number;
  /** A pointer in canvas content pixels, [x, y]. */
  cursor?: number[];
  /** What an agent acts on: `/twin/{case}/{lane}[/{field}]`, `run:{case}` or `workspace:{case}`. */
  focus?: string;
  case?: string;
  agent?: boolean;
  owner?: string;
  /** In words, such as "editing Grain boundary contrast"; never a value. */
  action?: string;
}

export interface ToolCall {
  id: string;
  name: string;
  args: string;
  done: boolean;
  result?: string;
  progress?: { progress: number; total?: number; message?: string };
}

export interface Interrupt {
  id: string;
  reason: string;
  message: string;
  metadata: {
    proposalId: string;
    author: string;
    onBehalfOf: string;
    lane: string;
    patch: PatchOp[];
    evidence: string[];
  };
}

// --- Agent platform wire types (additive; mirror consilith_contracts and agents/turn_events.py) --

export type ToolKind = "platform" | "plugin" | "skill" | "kb" | "ui" | "peer";
export type RiskClass = "R0" | "R1" | "R2" | "R3" | "R4";
export type ToolAvailability =
  | "available"
  | "blocked_by_drawer"
  | "blocked_by_activation"
  | "blocked_by_autonomy";

export interface ToolSpec {
  ref: string;
  alias: string;
  kind: ToolKind;
  description?: string;
  inputSchema?: Record<string, unknown>;
  risk: RiskClass;
  minAutonomy: Autonomy;
}

export interface CatalogEntry {
  spec: ToolSpec;
  availability?: ToolAvailability;
  /** Plain words for the person; set whenever the entry is blocked. */
  reason?: string;
  reachableVia?: { capability: string; mode: string }[];
}

export type PendingActionKind =
  | "confirm_run"
  | "confirm_claim"
  | "confirm_peer"
  | "confirm_lesson"
  | "confirm_edit"
  | "confirm_pin";

export type PendingActionStatus =
  | "pending"
  | "executing"
  | "done"
  | "declined"
  | "cancelled"
  | "superseded"
  | "expired";

export interface PendingAction {
  id: string;
  room: string;
  person: string;
  msgId: string;
  kind: PendingActionKind;
  call: Record<string, unknown>;
  resolvedArgs?: Record<string, unknown>;
  digest: string;
  inputRevisions?: Record<string, string>;
  pluginPins?: { entryPoint: string; version: string; digest: string; contractVersion?: "1" }[];
  taint?: boolean;
  origin?: "owner" | "event";
  status?: PendingActionStatus;
  result?: Record<string, unknown> | null;
  created: number;
  expires: number;
}

/** The small thread index the room document carries; bodies are fetched from the thread route. */
export interface PeerThreadIndex {
  id: string;
  participants: string[];
  count: number;
  lastTs: number;
  status: "open" | "closed";
}

export type ProviderAuth = "key" | "chatgpt" | "brought" | "none";

export interface ProviderDescriptor {
  id: string;
  label: string;
  auth: ProviderAuth;
  needsModel: boolean;
  defaultModel?: string;
  dataTerms: string;
}

/** The custom AG-UI event names the agent session emits (agents/turn_events.py). */
export type AgentEventName = "consilith.agentmsg" | "consilith.notice";

/** A typed problem or limit from a turn: plain words and what the person can do about it. */
export interface TurnNotice {
  code: string;
  message: string;
  fixAction?: string;
}

/** The body of a model error response (`error` in the JSON error shape). */
export interface ModelErrorBody {
  code: string;
  message: string;
  user_message: string;
  fix_action: string | null;
}
