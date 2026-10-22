/** Installed capability descriptors. Scientific definitions are supplied by packages. */
import { api } from "@/shared/http/client";
export type DisciplineId = string;
export type Autonomy = "observe" | "suggest" | "edit_my_lanes" | "run_solvers";
export type RoleDescriptor = {
  id: string;
  label: string;
  description?: string;
  autonomy?: Autonomy;
  goal?: string;
  skills?: string[];
};
export type PluginDescriptor = {
  id: string;
  version: string;
  title: string;
  description: string;
  capabilities: string[];
  artifactMediaTypes?: string[];
  roles: RoleDescriptor[];
  tools: string[];
  skills: { id: string; description?: string; path?: string }[];
  knowledge?: { id: string; title: string; mediaType?: string; path?: string }[];
};
export const PLUGINS: Record<string, PluginDescriptor> = {};
export const DISCIPLINES: Record<
  string,
  { label: string; skills: string[]; autonomy: Autonomy; goal: string; claims: string }
> = {};
export function roleInfo(id: string) {
  return (
    DISCIPLINES[id] ?? {
      label: id || "Choose in your room",
      skills: [],
      autonomy: "suggest" as Autonomy,
      goal: "",
      claims: "",
    }
  );
}
export function capabilityRoles(capability: string): RoleDescriptor[] {
  const cap = CAPABILITIES[capability];
  return (
    cap?.roles ??
    [
      ...new Set(
        cap?.lanes.map((l) => l.defaultRole ?? l.default_role ?? l.role ?? "").filter(Boolean) ??
          [],
      ),
    ].map((id) => ({ id, ...roleInfo(id) }))
  );
}
export function installPlugins(plugins: PluginDescriptor[]) {
  for (const key of Object.keys(PLUGINS)) delete PLUGINS[key];
  for (const key of Object.keys(DISCIPLINES)) delete DISCIPLINES[key];
  for (const plugin of plugins) {
    PLUGINS[plugin.id] = plugin;
    for (const role of plugin.roles)
      DISCIPLINES[role.id] = {
        label: role.label,
        skills: role.skills ?? [],
        autonomy: role.autonomy ?? "suggest",
        goal: role.goal ?? "",
        claims: role.description ?? "",
      };
  }
}
export const AUTONOMY: { id: Autonomy; label: string; note: string }[] = [
  { id: "observe", label: "Observe", note: "watches only" },
  { id: "suggest", label: "Suggest", note: "warns, proposes" },
  { id: "edit_my_lanes", label: "Edit my lanes", note: "changes your lanes" },
  { id: "run_solvers", label: "Run solvers", note: "also asks for solves" },
];
export type StudioId = string;
export type FieldDef = {
  name: string;
  label: string;
  kind:
    | "number"
    | "integer"
    | "text"
    | "choice"
    | "list"
    | "object"
    | "boolean"
    | "date"
    | "multichoice"
    | "artifact";
  unit?: string;
  /** A text field written as paragraphs. */
  multiline?: boolean;
  min?: number | null;
  max?: number | null;
  choices?: string[];
  required?: boolean;
  human_only?: boolean;
  review_quantity?: string;
  reviewQuantity?: string;
  humanOnly?: boolean;
  default?: unknown;
};
export type Capability = {
  pluginId?: string;
  roles?: RoleDescriptor[];
  id: string;
  label: string;
  grammar: string;
  description?: string;
  inputs: string[];
  lanes: {
    id: string;
    label: string;
    defaultRole?: string;
    default_role?: string;
    role?: string;
    fields: FieldDef[];
  }[];
  intakeBindings?: Record<
    string,
    {
      lane: string;
      field: string;
      valueKind: string;
      label?: string;
      mediaType?: string;
      metadataFields?: {
        name: string;
        label: string;
        kind: string;
        required?: boolean;
        minimumExclusive?: number;
      }[];
    }
  >;
};
export const CAPABILITIES: Record<string, Capability> = {};
export const STUDIOS: Record<
  string,
  { label: string; blurb: string; starts: string; lanes: string; grammar: string }
> = {};
export const STUDIO_LANES: Record<string, { id: string; label: string; role: string }[]> = {};
export const STUDIO_FIELDS: Record<string, Record<string, FieldDef[]>> = {};
export const OPEN_STUDIOS: string[] = [];
export function installCapabilities(capabilities: Capability[]) {
  for (const target of [CAPABILITIES, STUDIOS, STUDIO_LANES, STUDIO_FIELDS])
    for (const key of Object.keys(target)) delete target[key];
  OPEN_STUDIOS.splice(0);
  for (const cap of capabilities) {
    if (!cap.id || !Array.isArray(cap.lanes)) throw new Error("Invalid capability descriptor");
    CAPABILITIES[cap.id] = cap;
    STUDIOS[cap.id] = {
      label: cap.label,
      blurb: cap.description ?? cap.grammar,
      starts: cap.inputs.join(" · "),
      lanes: cap.lanes.map((l) => l.label).join(" · "),
      grammar: cap.grammar,
    };
    STUDIO_LANES[cap.id] = cap.lanes.map((l) => ({
      id: l.id,
      label: l.label,
      role: l.defaultRole ?? l.default_role ?? l.role ?? "",
    }));
    STUDIO_FIELDS[cap.id] = Object.fromEntries(cap.lanes.map((l) => [l.id, l.fields]));
    OPEN_STUDIOS.push(cap.id);
  }
}
export async function loadCapabilities() {
  const [result, plugins] = await Promise.all([
    api.get<{ capabilities: Capability[] }>("/capabilities"),
    api.get<{ plugins: PluginDescriptor[] }>("/plugins"),
  ]);
  installPlugins(plugins.plugins);
  installCapabilities(result.capabilities);
}

/** Capability ids are registry-unique; input kinds are local to their capability. */
export function intakeId(capability: string, kind: string): string {
  return JSON.stringify([capability, kind]);
}
