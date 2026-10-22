import { useState } from "react";
import { type A2UIAction, A2UISurface } from "@/shared/a2ui/Renderer";
import type { RunLive, Surface } from "@/shared/a2ui/types";
import { Button, Icon } from "@/shared/ui";
import { staleReasons } from "./InlineSurface";
import s from "./pinned.module.css";
import { type PinRecord, pinSurfaceId, pinsOf } from "./pins";
import type { RoomDoc } from "./types";

/** The views pinned for a person, above their studio. Each is the surface the room bound from
 * the stored refs, drawn by the same renderer as everything else. When what it was built from
 * changes, a chip says so and Refresh asks the room to bind the stored refs again. A view shared
 * with someone is theirs to read, and to edit only through lanes they own. */

export function PinnedViews({
  doc,
  me,
  viewer,
  surfaces,
  live,
  onAction,
  loadArtifact,
  uploadArtifact,
  onRefresh,
  onUnpin,
}: {
  doc: RoomDoc;
  me: string;
  viewer: string;
  surfaces: Record<string, Surface>;
  live?: Record<string, RunLive>;
  onAction?: (a: A2UIAction) => Promise<void>;
  loadArtifact?: (sha: string) => Promise<Blob>;
  uploadArtifact?: (file: File, mediaType: string) => Promise<{ sha256: string }>;
  /** Bind the stored refs of a pin again. Absent: no Refresh button. */
  onRefresh?: (pinId: string) => Promise<void>;
  onUnpin?: (pinId: string) => Promise<void>;
}) {
  const pins = pinsOf(doc, viewer);
  if (!pins.length) return null;
  return (
    <section className={s.pins} aria-label="Pinned views">
      <h2 className={s.heading}>Pinned</h2>
      {pins.map((pin) => (
        <Pinned
          key={pin.id}
          pin={pin}
          doc={doc}
          me={me}
          viewer={viewer}
          surface={surfaces[pinSurfaceId(pin.id, viewer)]}
          live={live}
          onAction={onAction}
          loadArtifact={loadArtifact}
          uploadArtifact={uploadArtifact}
          onRefresh={onRefresh}
          onUnpin={onUnpin}
        />
      ))}
    </section>
  );
}

function Pinned({
  pin,
  doc,
  me,
  viewer,
  surface,
  live,
  onAction,
  loadArtifact,
  uploadArtifact,
  onRefresh,
  onUnpin,
}: {
  pin: PinRecord;
  doc: RoomDoc;
  me: string;
  viewer: string;
  surface: Surface | undefined;
  live?: Record<string, RunLive> | undefined;
  onAction?: ((a: A2UIAction) => Promise<void>) | undefined;
  loadArtifact?: ((sha: string) => Promise<Blob>) | undefined;
  uploadArtifact?: ((file: File, mediaType: string) => Promise<{ sha256: string }>) | undefined;
  onRefresh?: ((pinId: string) => Promise<void>) | undefined;
  onUnpin?: ((pinId: string) => Promise<void>) | undefined;
}) {
  const [busy, setBusy] = useState<"refresh" | "unpin" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const name = (id: string) => doc.room.people[id]?.name ?? id;
  const mine = pin.owner === viewer;
  const reasons = surface ? staleReasons(surface, doc) : [];
  const editable = mine || pin.shares[viewer]?.editable === true;
  const sharedWith = Object.keys(pin.shares);
  const run = (kind: "refresh" | "unpin", fn: ((id: string) => Promise<void>) | undefined) => {
    if (!fn) return;
    setBusy(kind);
    setError(null);
    fn(pin.id)
      .catch((e) => setError(e instanceof Error ? e.message : "That did not work."))
      .finally(() => setBusy(null));
  };
  const sid = pinSurfaceId(pin.id, viewer);
  return (
    <article
      className={s.card}
      aria-label={pin.title || "Pinned view"}
      data-stale={reasons.length > 0}
    >
      <header className={s.head}>
        <span className={s.pin} aria-hidden="true" />
        <h3 className={s.title}>{pin.title || "Pinned view"}</h3>
        {reasons.length ? (
          <span className={s.stale} role="status" title={reasons.join("; ")}>
            <Icon name="warning" size={13} />
            Out of date
          </span>
        ) : null}
      </header>
      <p className={s.meta}>
        {mine ? "Pinned by you" : `Shared by ${name(pin.owner)}`}
        {pin.origin?.agent ? ` · written by ${pin.origin.agent}` : ""}
        {mine && sharedWith.length
          ? ` · shared with ${sharedWith
              .map((p) => `${name(p)} (${pin.shares[p]?.editable ? "can edit" : "read only"})`)
              .join(", ")}`
          : ""}
        {!mine ? (editable ? " · you can edit" : " · read only") : ""}
      </p>
      {surface ? (
        <div className={s.body}>
          <A2UISurface
            surface={surface}
            scope={`${doc.room.id}:${sid}`}
            {...(onAction && editable && viewer === me ? { onAction } : {})}
            {...(loadArtifact ? { loadArtifact } : {})}
            {...(uploadArtifact && editable ? { uploadArtifact } : {})}
            {...(live ? { live } : {})}
          />
        </div>
      ) : (
        <p className={s.rebuild} role="status">
          This view is being rebuilt from its saved references.
        </p>
      )}
      {reasons.length ? <p className={s.why}>{reasons.slice(0, 3).join(". ")}.</p> : null}
      {error ? (
        <p className={s.err} role="alert">
          {error}
        </p>
      ) : null}
      <footer className={s.foot}>
        {onRefresh && (reasons.length > 0 || !surface) ? (
          <Button
            variant="secondary"
            small
            disabled={busy !== null}
            onClick={() => run("refresh", onRefresh)}
          >
            {busy === "refresh" ? "Refreshing…" : "Refresh"}
          </Button>
        ) : null}
        {mine && onUnpin ? (
          <Button
            variant="ghost"
            small
            disabled={busy !== null}
            onClick={() => run("unpin", onUnpin)}
          >
            {busy === "unpin" ? "Unpinning…" : "Unpin"}
          </Button>
        ) : null}
      </footer>
    </article>
  );
}
