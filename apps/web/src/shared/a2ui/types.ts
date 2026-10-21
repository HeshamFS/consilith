/** A2UI v0.9, the room's closed catalog (apps/room/src/consilith_room/protocols/a2ui.py). */

export const CATALOG_ID = "https://consilith.local/a2ui/catalogs/consilith/v0_9";

export type Binding = { path: string };
export type TextValue = string | Binding;

export type A2UIMessage =
  | { version: "v0.9"; createSurface: { surfaceId: string; catalogId: string } }
  | { version: "v0.9"; updateComponents: { surfaceId: string; components: Component[] } }
  | { version: "v0.9"; updateDataModel: { surfaceId: string; path: string; value: unknown } }
  | { version: "v0.9"; deleteSurface: { surfaceId: string } };

export const PANELS = [
  "Panel",
  "SeatPanel",
  "AgentCard",
  "RouteCard",
  "RoutePlan",
  "ChecklistPanel",
  "ChecksPanel",
  "CandidatePanel",
  "FailurePanel",
  "TensionCard",
] as const;
export const DATA_VIEWS = [
  "PresencePill",
  "DecisionLog",
  "GhostCompletion",
  "HypothesisBars",
  "TradeOffFront",
  "WarningChip",
  "GhostProposal",
  "EventStream",
  "PermissionsMatrix",
  "MCPCallTable",
  "LedgerEntry",
  "MemoryList",
  "SchemaForm",
  "ParameterGrid",
  "ArtifactView",
  "ResultTable",
  "MetricGrid",
  "JobStatus",
  "EvidenceView",
] as const;

type Base = { id: string; accessibility?: Record<string, string>; weight?: number };

export type Component =
  | (Base & { component: "Column" | "Row"; children: string[] })
  | (Base & {
      component: "WorkspaceLayout";
      kind: "stack" | "grid" | "split" | "tabs" | "section";
      title: string;
      description: string;
      ratio: "balanced" | "focus-start" | "focus-end";
      emphasis: "plain" | "surface" | "hero" | "quiet";
      density: "comfortable" | "compact";
      children: string[];
      labels: string[];
    })
  | (Base & { component: "Card"; child: string })
  | (Base & {
      component: "Text";
      text: TextValue;
      variant?: "body" | "caption" | "h1" | "h2" | "h3";
    })
  | (Base & { component: "Divider" })
  | (Base & { component: "ValueRow"; label: string; value: Binding; note?: TextValue })
  | (Base & {
      component: "ApprovalChip";
      proposal: Binding;
      action: { event: { name: "decide"; context?: { proposalId: string } } };
    })
  | (Base & { component: "NextCalcChips"; chips: Binding })
  | (Base & {
      component: (typeof PANELS)[number];
      title: TextValue;
      stamp?: Binding;
      provenance?: Binding;
      phase?: Binding;
      children: string[];
    })
  | (Base & {
      component: (typeof DATA_VIEWS)[number];
      title?: TextValue;
      data: Binding;
      variant?: string;
    });

export type Surface = {
  id: string;
  catalogId: string;
  components: Record<string, Component>;
  data: unknown;
};

export type Quantity = {
  v: number;
  unit: string;
  provenanceId: string;
  stamp?: "verified" | "checking" | "warning" | "blocked";
  status?: string;
  tier?: 0 | 1 | 2;
  illustrative?: true;
};
