import { useEffect, useState } from "react";
import type { PluginDescriptor } from "@/shared/domain/disciplines";
import { api } from "@/shared/http/client";
import { Button, Icon } from "@/shared/ui";
import s from "./drawer.module.css";
import { Sheet } from "./Sheet";
import type { RoomConnection } from "./store";

type KnowledgeResource = {
  title: string;
  content?: string;
  contentBase64?: string;
  mediaType: string;
};
function Knowledge({ resource }: { resource: KnowledgeResource }) {
  const filename =
    Array.from(resource.title, (c) => (c.charCodeAt(0) < 32 ? "_" : c))
      .join("")
      .replace(/[\\/<>:"|?*]/g, "_") || "knowledge.bin";
  const [download, setDownload] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDownload(null);
    setError(null);
    if (resource.contentBase64 === undefined) return;
    let url: string;
    try {
      const bytes = Uint8Array.from(atob(resource.contentBase64), (c) => c.charCodeAt(0));
      url = URL.createObjectURL(new Blob([bytes], { type: resource.mediaType }));
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
      icon={<Icon name="drawer" size={24} />}
      onClose={onClose}
    >
      {error ? <p role="alert">{error}</p> : null}
      {resource ? <Knowledge resource={resource} /> : null}
      {!plugins ? (
        <p role="status">Loading available plugins…</p>
      ) : plugins.length === 0 && pinned.length === 0 ? (
        <p>No plugins are installed on this server.</p>
      ) : (
        [...pinned, ...plugins.filter((p) => !pinned.some((bound) => bound.id === p.id))].map(
          (plugin) => {
            const installed = plugins.find((p) => p.id === plugin.id);
            return (
              <section className={s.section} key={plugin.id} aria-label={plugin.title}>
                <h3 className={s.h3}>{plugin.title}</h3>
                <p className={s.small}>
                  Version {plugin.version}
                  {active.includes(plugin.id) ? " · pinned to this room" : ""}
                </p>
                {installed && installed.version !== plugin.version ? (
                  <p className={s.small}>
                    Installed version {installed.version} is available for new rooms.
                  </p>
                ) : null}
                {!installed ? (
                  <p role="status">
                    Unavailable on this server. This room retains its pinned descriptor.
                  </p>
                ) : null}
                <p>{plugin.description}</p>
                <p className={s.small}>{plugin.roles.map((role) => role.label).join(" · ")}</p>
                <p className={s.small}>
                  {plugin.capabilities.length} capabilities · {plugin.tools.length} tools ·{" "}
                  {plugin.skills.length} skills · {plugin.knowledge?.length ?? 0} knowledge
                  resources
                </p>
                <Button
                  disabled={!installed || active.includes(plugin.id) || busy !== null}
                  onClick={() => void activate(plugin)}
                >
                  {!installed
                    ? "Unavailable"
                    : active.includes(plugin.id)
                      ? "Active in this room"
                      : busy === plugin.id
                        ? "Activating…"
                        : `Activate ${plugin.title}`}
                </Button>
                {installed && active.includes(plugin.id)
                  ? plugin.knowledge?.map((item) => (
                      <Button
                        key={item.id}
                        variant="ghost"
                        onClick={() => void readKnowledge(item.id)}
                      >
                        {item.title}
                      </Button>
                    ))
                  : null}
              </section>
            );
          },
        )
      )}
    </Sheet>
  );
}
