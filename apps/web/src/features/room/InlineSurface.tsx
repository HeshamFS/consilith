import { useMemo, useState } from "react";
import { type A2UIAction, A2UISurface } from "@/shared/a2ui/Renderer";
import { applySurface, resolve } from "@/shared/a2ui/surface";
import type { Component, Surface } from "@/shared/a2ui/types";
import { Button, Icon } from "@/shared/ui";
import { TraceTree, TreeNote, TreeRow } from "./ChatTrace";
import s from "./inline-surface.module.css";
import type { RoomDoc } from "./types";

/** A surface the person's agent made in the chat. It is private to the person, shows a chip when
 * what it was built from has changed, and keeps its own field drafts. The surface is drawn by the
 * same renderer as the studio; Edit and Run reach the room through the person's own actions, and
 * an ad-hoc plan carries no approval or run control of its own. In the chat it is split into one
 * collapsed tree row per block (``SurfaceTree``); only a view the person asked to see is open. */

type Meta = {
  case?: string;
  title?: string;
  origin?: { agent?: string; model?: string };
  bound?: {
    results?: Record<string, { status?: string; stamp?: string }>;
    twin?: Record<string, unknown>;
  };
};

export function metaOf(surface: Surface | null): Meta {
  const data = surface?.data;
  if (!data || typeof data !== "object") return {};
  const meta = (data as { meta?: unknown }).meta;
  return meta && typeof meta === "object" ? (meta as Meta) : {};
}

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  return (
    ka.length === kb.length &&
    ka.every((k) => same((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
  );
}

/** Why the surface no longer matches the room: results it shows that changed status or stamp,
 * and input lanes whose values changed since it was built. Empty when it is still current. */
export function staleReasons(surface: Surface | null, doc: RoomDoc | undefined): string[] {
  if (!doc) return [];
  const bound = metaOf(surface).bound;
  const out: string[] = [];
  for (const [id, was] of Object.entries(bound?.results ?? {})) {
    const live = doc.results[id];
    if (!live) out.push("a result it shows was removed");
    else if (was.status && live.status !== was.status)
      out.push(`${live.quantity} is now ${live.status}`);
    else if (was.stamp && live.stamp !== was.stamp)
      out.push(`${live.quantity} is now ${live.stamp}`);
  }
  for (const [key, was] of Object.entries(bound?.twin ?? {})) {
    const [caseId = "", lane = ""] = key.split("/");
    if (!same(doc.twin[caseId]?.[lane] ?? {}, was)) out.push(`the ${key} inputs changed`);
  }
  return out;
}

type Comp = Component & Record<string, unknown>;

/** "written by a.agent (model)" captions: the reply's byline already says who wrote it. */
function isByline(c: Comp | undefined): boolean {
  return (
    !!c && c.component === "Text" && typeof c.text === "string" && /^written by /i.test(c.text)
  );
}

/** The surface without its attribution captions and its plan title (the row says it). */
export function withoutBylines(surface: Surface): Surface {
  const components: Record<string, Component> = {};
  const drop = new Set(
    Object.values(surface.components as Record<string, Comp>)
      .filter((c) => isByline(c) || c.id === "plan.title")
      .map((c) => c.id),
  );
  for (const [id, raw] of Object.entries(surface.components as Record<string, Comp>)) {
    if (drop.has(id)) continue;
    const kids = raw.children;
    components[id] = (
      Array.isArray(kids) ? { ...raw, children: kids.filter((k) => !drop.has(String(k))) } : raw
    ) as Component;
  }
  return { ...surface, components };
}

const VISUAL = new Set(["ArtifactView", "Chart", "Heatmap"]);

/** Whether a block draws a picture (an artifact image, a chart, a heat map). */
function isVisual(surface: Surface, id: string, depth = 0): boolean {
  const c = surface.components[id] as Comp | undefined;
  if (!c || depth > 12) return false;
  if (VISUAL.has(c.component)) return true;
  const kids = Array.isArray(c.children)
    ? (c.children as string[])
    : typeof c.child === "string"
      ? [c.child]
      : [];
  return kids.some((k) => isVisual(surface, k, depth + 1));
}

export type Block = { id: string; label: string; visual: boolean };

function titleOf(surface: Surface, c: Comp): string {
  if (typeof c.title === "string" && c.title) return c.title;
  if (typeof c.label === "string" && c.label) return c.label;
  const titled = (Array.isArray(c.children) ? (c.children as string[]) : [])
    .map((k) => surface.components[k] as Comp | undefined)
    .find((k) => k?.component === "Text" && typeof k.text === "string");
  if (titled && typeof titled.text === "string") return titled.text;
  const data = c.data as { path?: unknown } | undefined;
  if (data && typeof data.path === "string") {
    const value = resolve(surface.data, data.path) as { title?: unknown } | undefined;
    if (value && typeof value.title === "string" && value.title) return value.title;
  }
  return c.component;
}

/** The blocks of a surface: what its layouts place, in order, each with the name the layout or
 * the block gives it. A surface with no layout is its root's children. */
export function blocksOf(surface: Surface): Block[] {
  const out: Block[] = [];
  const walk = (ids: string[], labels: string[], depth: number) => {
    ids.forEach((id, i) => {
      const c = surface.components[id] as Comp | undefined;
      if (!c || isByline(c) || id === "plan.title") return;
      const isLayout =
        c.component === "WorkspaceLayout" || (c.component === "Column" && id.startsWith("layout."));
      if (isLayout && depth < 8) {
        const kids = Array.isArray(c.children) ? (c.children as string[]) : [];
        const ls = Array.isArray(c.labels) ? (c.labels as string[]) : [];
        walk(kids, ls, depth + 1);
        return;
      }
      out.push({
        id,
        label: labels[i] || titleOf(surface, c),
        visual: isVisual(surface, id),
      });
    });
  };
  const root = surface.components.root as Comp | undefined;
  walk(Array.isArray(root?.children) ? (root.children as string[]) : [], [], 0);
  return out;
}

/** One block as a surface of its own: the same data, a root that holds only that block. */
function blockSurface(surface: Surface, id: string): Surface {
  return {
    ...surface,
    components: {
      ...surface.components,
      root: { id: "root", component: "Column", children: [id] } as Component,
    },
  };
}

type Actions = {
  scope?: string;
  onAction?: (a: A2UIAction) => Promise<void>;
  loadArtifact?: (sha: string) => Promise<Blob>;
};

function Body({ surface, scope, onAction, loadArtifact }: Actions & { surface: Surface }) {
  return (
    <div className={s.body}>
      <A2UISurface
        surface={surface}
        scope={scope ?? surface.id}
        assemble
        {...(onAction ? { onAction } : {})}
        {...(loadArtifact ? { loadArtifact } : {})}
      />
    </div>
  );
}

function useSurface(activity: { surfaceId: string; messages: unknown[] }): Surface | null {
  return useMemo(() => {
    const surface = applySurface(undefined, activity.surfaceId, activity.messages);
    return surface ? withoutBylines(surface) : null;
  }, [activity.surfaceId, activity.messages]);
}

function Foot({
  meta,
  reasons,
  onRefresh,
  onPin,
  onOpenStudio,
}: {
  meta: Meta;
  reasons: string[];
  onRefresh?: (() => Promise<void>) | undefined;
  onPin?: (() => void) | undefined;
  onOpenStudio?: ((caseId: string) => void) | undefined;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      {error ? (
        <p className={s.err} role="alert">
          {error}
        </p>
      ) : null}
      <footer className={s.foot}>
        {reasons.length && onRefresh ? (
          <Button
            variant="secondary"
            small
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setError(null);
              onRefresh()
                .catch((e) => setError(e instanceof Error ? e.message : "Could not refresh"))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? "Refreshing…" : "Refresh"}
          </Button>
        ) : null}
        <Button
          variant="ghost"
          small
          disabled={!onPin}
          title={onPin ? "Pin this to the room" : "Pinning arrives with shared surfaces"}
          onClick={onPin}
        >
          Pin
        </Button>
        {meta.case && onOpenStudio ? (
          <Button variant="ghost" small onClick={() => onOpenStudio(meta.case ?? "")}>
            Open in studio
          </Button>
        ) : null}
      </footer>
    </>
  );
}

type Props = {
  /** The ACTIVITY_SNAPSHOT content of the agent's surface. */
  activity: { surfaceId: string; messages: unknown[] };
  /** The live room, to tell when the surface is out of date. */
  doc?: RoomDoc | undefined;
  /** Where this surface's field drafts live; each surface has its own. */
  scope?: string;
  onAction?: (a: A2UIAction) => Promise<void>;
  loadArtifact?: (sha: string) => Promise<Blob>;
  /** Bind the stored refs again against the live room. */
  onRefresh?: () => Promise<void>;
  /** Pin the surface for the room. A stub until pinning lands. */
  onPin?: () => void;
  onOpenStudio?: (caseId: string) => void;
};

function attribution(meta: Meta): string {
  const who = meta.origin?.agent ?? "your agent";
  return `Written by ${who}${meta.origin?.model ? ` (${meta.origin.model})` : ""}`;
}

/** The whole surface as one compact card (a pinned view, or anything shown on its own). */
export function InlineSurface({
  activity,
  doc,
  scope,
  onAction,
  loadArtifact,
  onRefresh,
  onPin,
  onOpenStudio,
}: Props) {
  const surface = useSurface(activity);
  if (!surface)
    return (
      <p className={s.broken} role="status">
        This view could not be shown.
      </p>
    );
  const meta = metaOf(surface);
  const reasons = staleReasons(surface, doc);
  return (
    <article
      className={s.card}
      aria-label={meta.title || "Shown by your agent"}
      title={attribution(meta)}
    >
      <header className={s.head}>
        <span className={s.title}>{meta.title || "Shown by your agent"}</span>
        <span className={s.private}>Only you</span>
        {reasons.length ? (
          <span className={s.stale} role="status" title={reasons.join("; ")}>
            <Icon name="warning" size={12} />
            Out of date
          </span>
        ) : null}
      </header>
      <Body
        surface={surface}
        {...(scope ? { scope } : {})}
        {...(onAction ? { onAction } : {})}
        {...(loadArtifact ? { loadArtifact } : {})}
      />
      {reasons.length ? <p className={s.why}>{reasons.slice(0, 3).join(". ")}.</p> : null}
      <Foot
        meta={meta}
        reasons={reasons}
        onRefresh={onRefresh}
        onPin={onPin}
        onOpenStudio={onOpenStudio}
      />
    </article>
  );
}

/** The surface in the chat: one collapsed tree row per block; a picture the person asked to see
 * (`openVisual`) is the one block shown open, its height capped. Staleness is one muted line. */
export function SurfaceTree({ openVisual = false, ...props }: Props & { openVisual?: boolean }) {
  const surface = useSurface(props.activity);
  if (!surface)
    return (
      <p className={s.broken} role="status">
        This view could not be shown.
      </p>
    );
  const meta = metaOf(surface);
  const reasons = staleReasons(surface, props.doc);
  const blocks = blocksOf(surface);
  const shown = openVisual ? blocks.find((b) => b.visual) : undefined;
  const actions: Actions = {
    scope: props.scope ?? surface.id,
    ...(props.onAction ? { onAction: props.onAction } : {}),
    ...(props.loadArtifact ? { loadArtifact: props.loadArtifact } : {}),
  };
  return (
    <section
      className={s.group}
      aria-label={meta.title || "Shown by your agent"}
      title={attribution(meta)}
      data-testid="surface-tree"
    >
      {shown ? (
        <article className={s.open} aria-label={shown.label} data-testid="surface-open">
          <span className={s.openTitle}>{shown.label}</span>
          <Body surface={blockSurface(surface, shown.id)} {...actions} />
        </article>
      ) : null}
      <TraceTree label={meta.title || "Views"}>
        {blocks
          .filter((b) => b !== shown)
          .map((b) => (
            <TreeRow
              key={b.id}
              kind={b.visual ? "View" : "Result"}
              name={b.label.replaceAll("_", " ")}
              status="only you"
              testId="surface-row"
            >
              <Body surface={blockSurface(surface, b.id)} {...actions} />
            </TreeRow>
          ))}
        {reasons.length ? (
          <TreeNote>
            <Icon name="warning" size={11} /> Out of date: {reasons.slice(0, 3).join(". ")}.
          </TreeNote>
        ) : null}
      </TraceTree>
      <Foot
        meta={meta}
        reasons={reasons}
        onRefresh={props.onRefresh}
        onPin={props.onPin}
        onOpenStudio={props.onOpenStudio}
      />
    </section>
  );
}
