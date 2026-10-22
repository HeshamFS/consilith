import { useEffect, useState } from "react";
import type { PluginDescriptor } from "@/shared/domain/disciplines";
import { api } from "@/shared/http/client";
import { Button, Icon } from "@/shared/ui";
import { studioIcon } from "./Deck";
import s from "./drawer.module.css";
import c from "./plugin-sheet.module.css";
import { Sheet } from "./Sheet";
import type { RoomConnection } from "./store";

type KnowledgeResource = {
  title: string;
  content?: string;
  contentBase64?: string;
  mediaType: string;
};
/** A plugin's declared type is only a claim: a blob typed text/html or image/svg+xml opens as a
 * page in this app's origin. Downloads are always opaque bytes with a name that stays in the
 * download folder, names no device and does not end in an extension a system would run. */
const OPAQUE = "application/octet-stream";
const ACTIVE_EXTENSION =
  /\.(html?|xhtml|svg|xml|js|mjs|cjs|php|hta|exe|msi|bat|cmd|com|scr|ps1|vbs|jar|lnk|sh|app|dmg|apk)$/i;
const DEVICE_NAME = /^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$/i;
export function safeDownloadName(title: string): string {
  let name = Array.from(title, (c) => (c.charCodeAt(0) < 32 || c.charCodeAt(0) === 127 ? "_" : c))
    .join("")
    .replace(/[\\/<>:"|?*]/g, "_")
    .replace(/[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g, "")
    .replace(/^[.\s]+|[.\s]+$/g, "")
    .slice(0, 120);
  if (!name) return "knowledge.bin";
  if (DEVICE_NAME.test(name)) name = `_${name}`;
  return ACTIVE_EXTENSION.test(name) ? `${name}.bin` : name;
}
function Knowledge({ resource }: { resource: KnowledgeResource }) {
  const filename = safeDownloadName(resource.title);
  const [download, setDownload] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDownload(null);
    setError(null);
    if (resource.contentBase64 === undefined) return;
    let url: string;
    try {
      const bytes = Uint8Array.from(atob(resource.contentBase64), (c) => c.charCodeAt(0));
      url = URL.createObjectURL(new Blob([bytes], { type: OPAQUE }));
      setDownload(url);
    } catch {
      setError("This resource could not be decoded for download.");
      return;
    }
    return () => URL.revokeObjectURL(url);
  }, [resource]);
  return (
    <section className={s.section} aria-label="Plugin knowledge">
      <h3 className={s.h3}>{resource.title}</h3>
      <p className={s.small}>{resource.mediaType}</p>
      {resource.content !== undefined ? (
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{resource.content}</pre>
      ) : download ? (
        <a href={download} download={filename}>
          Download {resource.title}
        </a>
      ) : error ? (
        <p role="alert">{error}</p>
      ) : (
        <p>Preparing download…</p>
      )}
    </section>
  );
}

/** Whether the installed release differs from what the room pinned: the version, or the same
 * version with a changed descriptor (a renamed studio, a new output). */
export function differs(installed: PluginDescriptor, pinned: PluginDescriptor): boolean {
  if (installed.version !== pinned.version || installed.title !== pinned.title) return true;
  return JSON.stringify(installed.capabilities) !== JSON.stringify(pinned.capabilities);
}

export function PluginSheet({
  conn,
  active,
  pinned,
  onClose,
}: {
  conn: RoomConnection;
  active: string[];
  pinned: PluginDescriptor[];
  onClose: () => void;
}) {
  const [plugins, setPlugins] = useState<PluginDescriptor[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resource, setResource] = useState<KnowledgeResource | null>(null);
  const [updated, setUpdated] = useState<string | null>(null);
  // What the room's pins would change to, from the server (it compares package digests too).
  const [pending, setPending] = useState<{
    changed: Record<string, unknown>;
    owner: string | null;
  }>({ changed: {}, owner: null });
  // biome-ignore lint/correctness/useExhaustiveDependencies: refetch after an update
  useEffect(() => {
    api
      .get<{ changed: Record<string, unknown>; owner: string | null }>(
        `/rooms/${encodeURIComponent(conn.room)}/plugins/updates`,
      )
      .then(
        (r) => setPending({ changed: r.changed ?? {}, owner: r.owner ?? null }),
        () => setPending({ changed: {}, owner: null }),
      );
  }, [conn.room, updated]);
  useEffect(() => {
    api.get<{ plugins: PluginDescriptor[] }>("/plugins").then(
      (r) => setPlugins(r.plugins),
      (e) => setError(e instanceof Error ? e.message : "Plugins could not be loaded."),
    );
  }, []);
  async function activate(plugin: PluginDescriptor) {
    setBusy(plugin.id);
    setError(null);
    try {
      await conn.activatePlugin(plugin.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Activation failed.");
    } finally {
      setBusy(null);
    }
  }
  async function update() {
    setBusy("update");
    setError(null);
    setUpdated(null);
    try {
      const r = await api.post<{ changed: Record<string, unknown> }>(
        `/rooms/${encodeURIComponent(conn.room)}/plugins/migrate`,
      );
      setUpdated(
        Object.keys(r.changed ?? {}).length
          ? "Updated. The room now uses the installed releases."
          : "Already on the installed releases.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "The update did not go through.");
    } finally {
      setBusy(null);
    }
  }
  async function readKnowledge(id: string) {
    setError(null);
    try {
      setResource(
        await api.get(
          `/rooms/${encodeURIComponent(conn.room)}/knowledge/${encodeURIComponent(id)}`,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Knowledge could not be opened.");
    }
  }
  return (
    <Sheet
      title="Room plugins"
      sub="Add capabilities, tools and knowledge to this shared workspace."
      icon={<Icon name="drawer" size={20} />}
      onClose={onClose}
    >
      {error ? <p role="alert">{error}</p> : null}
      {updated ? <p role="status">{updated}</p> : null}
      {resource ? <Knowledge resource={resource} /> : null}
      {!plugins ? (
        <p role="status">Loading available plugins…</p>
      ) : plugins.length === 0 && pinned.length === 0 ? (
        <p>No plugins are installed on this server.</p>
      ) : (
        <ul className={c.cards}>
          {[...pinned, ...plugins.filter((p) => !pinned.some((bound) => bound.id === p.id))].map(
            (plugin) => {
              const installed = plugins.find((p) => p.id === plugin.id);
              const on = active.includes(plugin.id);
              const stale =
                !!installed &&
                on &&
                (differs(installed, plugin) || Object.keys(pending.changed).length > 0) &&
                (pending.owner === null || pending.owner === conn.person);
              const first = plugin.capabilities[0];
              const icon = studioIcon({
                id: typeof first === "string" ? first : plugin.id,
                label: plugin.title,
                pluginId: plugin.id,
              });
              return (
                <li key={plugin.id}>
                  <section
                    className={c.card}
                    data-on={on ? "true" : undefined}
                    aria-label={plugin.title}
                  >
                    <header className={c.cardHead}>
                      <span className={c.icon} aria-hidden>
                        <Icon name={icon} size={18} />
                      </span>
                      <div className={c.titles}>
                        <h3 className={c.title}>{plugin.title}</h3>
                        <p className={c.meta}>
                          <span className={c.badge}>v{plugin.version}</span>
                          {on ? (
                            <span className={c.badge} data-tone="on">
                              Pinned to this room
                            </span>
                          ) : installed ? (
                            <span className={c.badge}>Available</span>
                          ) : null}
                          {!installed ? (
                            <span className={c.badge} data-tone="warn">
                              Not on this server
                            </span>
                          ) : null}
                        </p>
                      </div>
                      <Button
                        small
                        variant={on ? "ghost" : "primary"}
                        disabled={!installed || on || busy !== null}
                        aria-label={
                          !installed
                            ? "Unavailable"
                            : on
                              ? "Active in this room"
                              : `Activate ${plugin.title}`
                        }
                        onClick={() => void activate(plugin)}
                      >
                        {!installed
                          ? "Unavailable"
                          : on
                            ? "Active"
                            : busy === plugin.id
                              ? "Activating…"
                              : "Activate"}
                      </Button>
                    </header>
                    <p className={c.desc}>{plugin.description}</p>
                    <p className={c.chips}>
                      {plugin.roles.map((role) => (
                        <span key={role.id ?? role.label} className={c.chip}>
                          {role.label}
                        </span>
                      ))}
                      <span className={c.count}>
                        {plugin.capabilities.length} studio
                        {plugin.capabilities.length === 1 ? "" : "s"} · {plugin.tools.length} tools
                        · {plugin.skills.length} skills
                      </span>
                    </p>
                    {!installed ? (
                      <p className={c.note} role="status">
                        Unavailable on this server. This room retains its pinned descriptor.
                      </p>
                    ) : null}
                    {stale && installed ? (
                      <div className={c.update}>
                        <span>
                          {installed.version !== plugin.version
                            ? `Version ${installed.version} is installed.`
                            : "A newer build of this release is installed."}
                        </span>
                        <Button small disabled={busy !== null} onClick={() => void update()}>
                          {busy === "update" ? "Updating…" : "Update to the installed release"}
                        </Button>
                      </div>
                    ) : null}
                    {installed && on && plugin.knowledge?.length ? (
                      <div className={c.knowledge}>
                        <span className={c.count}>Knowledge</span>
                        {plugin.knowledge.map((item) => (
                          <button
                            key={item.id}
                            type="button"
                            className={c.chipBtn}
                            onClick={() => void readKnowledge(item.id)}
                          >
                            {item.title}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </section>
                </li>
              );
            },
          )}
        </ul>
      )}
    </Sheet>
  );
}
