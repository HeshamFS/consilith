import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { type A2UIAction, A2UISurface } from "@/shared/a2ui/Renderer";
import type { Surface } from "@/shared/a2ui/types";
import { rawFetch } from "@/shared/http/client";
import { Button, Mark } from "@/shared/ui";
import type { RoomConnection } from "./store";
import s from "./studio.module.css";
import type { CaseId, RoomDoc, ToolCall } from "./types";

/** All working controls and views come from a validated capability-bound A2UI surface. */
export function StudioView({
  doc,
  me,
  viewAs,
  caseId,
  conn,
  surfaces,
}: {
  doc: RoomDoc;
  me: string;
  viewAs: string;
  caseId: CaseId;
  conn: RoomConnection;
  surfaces: Record<string, Surface>;
  tools: Record<string, ToolCall>;
}) {
  const viewer = doc.room.people[viewAs] ? viewAs : me;
  const sid = `${caseId}:${viewer}:main`;
  const surface = surfaces[sid];
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const personal = doc.room.agents[`${me}.agent`];
  const modelIdentity =
    personal?.kind === "personal"
      ? `${personal.config.modelProvider}:${personal.config.modelName ?? ""}:${personal.config.goal}`
      : "";
  const boundInputs = doc.twin[caseId];
  useEffect(() => {
    if (!modelIdentity || !boundInputs) return;
    let stopped = false;
    setError(null);
    void conn.surfaces(caseId, viewer === me ? undefined : viewer).catch((e) => {
      if (!stopped) setError(e instanceof Error ? e.message : "Workspace composition failed");
    });
    return () => {
      stopped = true;
    };
  }, [caseId, viewer, me, conn, modelIdentity, boundInputs]);
  const loadArtifact = useCallback(
    async (sha: string) => {
      if (!/^[a-f0-9]{64}$/.test(sha)) throw new Error("Invalid artifact reference");
      const response = await rawFetch(`/rooms/${encodeURIComponent(doc.room.id)}/artifacts/${sha}`);
      if (!response.ok) throw new Error("Artifact could not be loaded");
      return response.blob();
    },
    [doc.room.id],
  );
  async function act(action: A2UIAction) {
    if (viewer !== me) throw new Error("Switch to your own view to act");
    if (action.name === "decide")
      await conn.decide(action.proposalId, action.accept, action.reason);
    else if (action.name === "edit")
      await conn.editTwin(
        [{ op: action.present ? "replace" : "add", path: action.path, value: action.value }],
        action.reason,
      );
    else {
      const result = (await conn.solve(action.capability)) as { error?: string };
      if (result?.error) throw new Error(result.error);
    }
    await conn.surfaces(caseId);
  }
  const meta =
    surface?.data && typeof surface.data === "object"
      ? (
          surface.data as {
            meta?: {
              composition?: { kind: string; detail: string; code?: string };
              assembly?: string;
              workspaceTitle?: string;
              workspaceIntent?: string;
            };
          }
        ).meta
      : undefined;
  return (
    <div className={s.studio}>
      <header className={s.head}>
        <div>
          <p className={s.caps}>A shared model · your perspective</p>
          <h1 className={s.title}>{meta?.workspaceTitle || "Your workspace"}</h1>
          <p className={s.sub}>
            {meta?.workspaceIntent || "Your agent is making room for the task ahead."}
          </p>
        </div>
        <span className={s.assemblyBadge} role="status">
          {meta?.composition?.kind === "model" && meta?.assembly === "ready"
            ? "Agent composed"
            : meta?.composition?.kind === "preview"
              ? "Descriptor preview · no model"
              : meta?.assembly === "model_required"
                ? "Model connection needed"
                : "Assembling workspace"}
        </span>
      </header>
      {!surface || meta?.assembly !== "ready" ? (
        <section className={s.assemblyCard}>
          <Mark
            size={68}
            loading={meta?.assembly !== "model_required" && meta?.assembly !== "composition_failed"}
          />
          <p className={s.caps}>Consilith workspace</p>
          <h2>
            {meta?.assembly === "model_required"
              ? "Let your agent take the first step."
              : meta?.assembly === "composition_failed"
                ? "Your model needs attention."
                : "Making space for your next idea."}
          </h2>
          <p>
            {meta?.assembly === "model_required"
              ? "Connect a model in your agent settings. It will compose a workspace around this task, your inputs and the evidence you need."
              : meta?.assembly === "composition_failed"
                ? meta?.composition?.detail
                : "Your agent is choosing the controls, model views and evidence panels. Nothing runs until you ask."}
          </p>
          {(meta?.assembly === "model_required" || meta?.assembly === "composition_failed") &&
          viewer === me ? (
            <div className={s.assemblyActions}>
              <Link to="/settings/agent" className={s.settingsLink}>
                Open provider settings
              </Link>
              <Button
                disabled={composing}
                onClick={() => {
                  setComposing(true);
                  void conn
                    .compose(caseId, "")
                    .catch((e) => setError(String(e)))
                    .finally(() => setComposing(false));
                }}
              >
                {meta?.composition?.code === "subscription_sharing_usage_limit_exceeded"
                  ? "Retry after updating limits"
                  : "Try connected model"}
              </Button>
              <Button
                variant="ghost"
                disabled={composing}
                onClick={() => {
                  setComposing(true);
                  void conn
                    .compose(caseId, "", true)
                    .catch((e) => setError(String(e)))
                    .finally(() => setComposing(false));
                }}
              >
                Preview controls without a model
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}
      {error ? (
        <p role="alert" className={s.bad}>
          {error}
        </p>
      ) : null}
      {surface ? (
        <A2UISurface
          surface={surface}
          scope={`${doc.room.id}:${sid}`}
          onAction={viewer === me && !composing && meta?.assembly === "ready" ? act : undefined}
          loadArtifact={loadArtifact}
        />
      ) : (
        <div role="status">
          Assembling controls, artifact views and evidence panels before execution…
        </div>
      )}
    </div>
  );
}
