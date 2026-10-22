import {
  type A2UIMessage,
  CATALOG_ID,
  type Component,
  DATA_VIEWS,
  LAYOUT_DENSITIES,
  LAYOUT_EMPHASES,
  LAYOUT_KINDS,
  LAYOUT_RATIOS,
  MAX_COMPONENTS,
  MAX_DEPTH,
  PANELS,
  type Surface,
  TEXT_VARIANTS,
  VARIANTS,
} from "./types";

/** Applies a batch of A2UI messages to a surface. The room publishes complete replacements
 * (createSurface, updateComponents, updateDataModel), so a batch normally starts empty.
 * Returns null when the surface is deleted or the result is not a valid tree. Every message
 * envelope and every component is checked against the closed catalog before React sees it:
 * a malformed one, an unknown component, a dangling child, a cycle or an oversized tree makes
 * the whole surface invalid rather than half shown. Nothing here throws. */
export function applySurface(
  base: Surface | undefined,
  surfaceId: string,
  messages: unknown[],
): Surface | null {
  try {
    let s: Surface | null = base ? { ...base, components: { ...base.components } } : null;
    for (const raw of messages) {
      const m = envelope(raw);
      if (!m) return null;
      if ("createSurface" in m) {
        if (m.createSurface.surfaceId !== surfaceId) continue;
        if (m.createSurface.catalogId !== CATALOG_ID) return null;
        s = { id: surfaceId, catalogId: CATALOG_ID, components: {}, data: {} };
      } else if ("updateComponents" in m) {
        if (!s || m.updateComponents.surfaceId !== surfaceId) continue;
        for (const c of m.updateComponents.components) {
          if (!component(c)) return null;
          s.components[c.id] = c;
        }
      } else if ("updateDataModel" in m) {
        if (!s || m.updateDataModel.surfaceId !== surfaceId) continue;
        const path = m.updateDataModel.path || "/";
        s.data =
          path === "/" ? m.updateDataModel.value : setAt(s.data, path, m.updateDataModel.value);
      } else if (m.deleteSurface.surfaceId === surfaceId) {
        return null;
      }
    }
    return s && tree(s) ? s : null;
  } catch {
    return null;
  }
}

const MAX_RENDERED = 2000;
const PANEL_SET = new Set<string>(PANELS);
const VIEW_SET = new Set<string>(DATA_VIEWS);

type Rec = Record<string, unknown>;
const isObj = (v: unknown): v is Rec => !!v && typeof v === "object" && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === "string";
const isBinding = (v: unknown) => isObj(v) && isStr(v.path) && v.path.startsWith("/");
const isText = (v: unknown) => isStr(v) || isBinding(v);
const isIds = (v: unknown) => Array.isArray(v) && v.every(isStr);
const optional = (v: unknown, check: (x: unknown) => boolean) => v === undefined || check(v);
const isOneOf = (list: readonly string[], v: unknown) => isStr(v) && list.includes(v);
/** A view that declares variants refuses any other; a view that declares none ignores it. */
const honoured = (component: string, v: string) => {
  const allowed = VARIANTS[component];
  return !allowed || allowed.includes(v);
};

function envelope(raw: unknown): A2UIMessage | null {
  if (!isObj(raw) || raw.version !== "v0.9") return null;
  const kinds = ["createSurface", "updateComponents", "updateDataModel", "deleteSurface"].filter(
    (k) => k in raw,
  );
  if (kinds.length !== 1) return null;
  const body = raw[kinds[0] as string];
  if (!isObj(body) || !isStr(body.surfaceId)) return null;
  switch (kinds[0]) {
    case "createSurface":
      return isStr(body.catalogId) ? (raw as A2UIMessage) : null;
    case "updateComponents":
      return Array.isArray(body.components) && body.components.length <= MAX_COMPONENTS
        ? (raw as A2UIMessage)
        : null;
    case "updateDataModel":
      return optional(body.path, isStr) && "value" in body ? (raw as A2UIMessage) : null;
    default:
      return raw as A2UIMessage;
  }
}

/** One component against its catalog entry: required props present and of the right kind. */
export function component(c: unknown): c is Component {
  if (!isObj(c) || !isStr(c.id) || !c.id || !isStr(c.component)) return false;
  switch (c.component) {
    case "WorkspaceLayout":
      return (
        isIds(c.children) &&
        isIds(c.labels) &&
        c.labels.length === c.children.length &&
        isStr(c.title) &&
        isStr(c.description) &&
        isOneOf(LAYOUT_KINDS, c.kind) &&
        isOneOf(LAYOUT_RATIOS, c.ratio) &&
        isOneOf(LAYOUT_EMPHASES, c.emphasis) &&
        isOneOf(LAYOUT_DENSITIES, c.density)
      );
    case "Column":
    case "Row":
      return isIds(c.children);
    case "Card":
      return isStr(c.child);
    case "Divider":
      return true;
    case "Text":
      return isText(c.text) && optional(c.variant, (v) => isOneOf(TEXT_VARIANTS, v));
    case "ValueRow":
      return isStr(c.label) && isBinding(c.value) && optional(c.note, isText);
    case "ApprovalChip": {
      const ev = isObj(c.action) && isObj(c.action.event) ? c.action.event : null;
      return isBinding(c.proposal) && !!ev && ev.name === "decide";
    }
    case "NextCalcChips":
      return isBinding(c.chips);
    default:
      if (PANEL_SET.has(c.component))
        return (
          isText(c.title) &&
          isIds(c.children) &&
          optional(c.stamp, isBinding) &&
          optional(c.provenance, isBinding) &&
          optional(c.phase, isBinding)
        );
      if (VIEW_SET.has(c.component))
        return (
          isBinding(c.data) &&
          optional(c.title, isText) &&
          optional(c.variant, (v) => isStr(v) && honoured(c.component as string, v))
        );
      return false;
  }
}

/** One root, every child present, no cycles, bounded depth and a bounded rendered size. A
 * component shared by several parents renders once per parent, so the expanded count is what
 * the renderer pays. Each subtree's size and height are memoised; the root's height is the
 * deepest any path goes, whichever order the shared subtrees are visited in. */
function tree(s: Surface): boolean {
  if (!s.components.root || Object.keys(s.components).length > MAX_COMPONENTS) return false;
  const onPath = new Set<string>();
  const memo = new Map<string, { size: number; height: number }>();
  const BAD = { size: Number.POSITIVE_INFINITY, height: Number.POSITIVE_INFINITY };
  const walk = (id: string, depth: number): { size: number; height: number } => {
    if (depth > MAX_DEPTH || onPath.has(id)) return BAD;
    const known = memo.get(id);
    if (known) return depth + known.height > MAX_DEPTH ? BAD : known;
    const c = s.components[id];
    if (!c) return BAD;
    onPath.add(id);
    let size = 1;
    let height = 0;
    for (const k of childrenOf(c)) {
      const sub = walk(k, depth + 1);
      size += sub.size;
      height = Math.max(height, sub.height + 1);
      if (size > MAX_RENDERED || depth + height > MAX_DEPTH) break;
    }
    onPath.delete(id);
    const out = { size, height };
    memo.set(id, out);
    return out;
  };
  const r = walk("root", 0);
  return r.size <= MAX_RENDERED && r.height <= MAX_DEPTH;
}

export function childrenOf(c: Component): string[] {
  if ("children" in c && Array.isArray(c.children)) return c.children;
  if ("child" in c && typeof c.child === "string") return [c.child];
  return [];
}

/** Keys that would reach an object's prototype; a path using one is ignored. */
const UNSAFE = new Set(["__proto__", "prototype", "constructor"]);

function setAt(doc: unknown, pointer: string, value: unknown): unknown {
  const keys = pointer
    .slice(1)
    .split("/")
    .map((t) => t.replace(/~1/g, "/").replace(/~0/g, "~"));
  if (keys.some((k) => UNSAFE.has(k))) return doc;
  const root: Record<string, unknown> =
    doc && typeof doc === "object" ? structuredClone(doc as Record<string, unknown>) : {};
  let cur = root;
  keys.forEach((k, i) => {
    if (i === keys.length - 1) cur[k] = value;
    else {
      const next = Object.hasOwn(cur, k) ? cur[k] : undefined;
      cur[k] = next && typeof next === "object" ? next : {};
      cur = cur[k] as Record<string, unknown>;
    }
  });
  return root;
}

/** Resolves a JSON Pointer binding in the data model; undefined when it does not resolve. */
export function resolve(data: unknown, path: string): unknown {
  if (path === "/" || path === "") return data;
  let cur: unknown = data;
  for (const raw of path.slice(1).split("/")) {
    const k = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    if (cur && typeof cur === "object" && Object.hasOwn(cur, k))
      cur = (cur as Record<string, unknown>)[k];
    else return undefined;
  }
  return cur;
}
