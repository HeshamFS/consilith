import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { AssemblyStage, type StageState } from "@/shared/a2ui/AssemblyStage";
import { A2UISurface } from "@/shared/a2ui/Renderer";
import type { Surface } from "@/shared/a2ui/types";
import { Button, Mark, seatVar } from "@/shared/ui";
import { PinnedViews } from "./PinnedViews";
import { liveRuns } from "./pins";
import type { RoomConnection } from "./store";
import s from "./studio.module.css";
import type { CaseId, RoomDoc, ToolCall } from "./types";
import { useSurfaceActions } from "./useSurfaceActions";

/** All working controls and views come from a validated capability-bound A2UI surface. */
export function StudioView({
  doc,
  me,
  viewAs,
  caseId,
  conn,
  surfaces,
  tools,
  onRefreshPin,
  onUnpin,
}: {
  doc: RoomDoc;
  me: string;
  viewAs: string;
  caseId: CaseId;
  conn: RoomConnection;
  surfaces: Record<string, Surface>;
  tools: Record<string, ToolCall>;
  /** Ask the room to bind a pinned view's stored refs again. Absent: no Refresh button. */
  onRefreshPin?: (pinId: string) => Promise<void>;
  onUnpin?: (pinId: string) => Promise<void>;
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
  const { act, loadArtifact } = useSurfaceActions({
    conn,
    roomId: doc.room.id,
    me,
    viewer,
    onDone: () => conn.surfaces(caseId),
  });
  const uploadArtifact = useCallback(
    (file: File, mediaType: string) => conn.upload(file, mediaType),
    [conn],
  );
  const live = useMemo(() => liveRuns(tools, doc.queue), [tools, doc.queue]);
  const actionable = viewer === me;
  const meta =
    surface?.data && typeof surface.data === "object"
      ? (
          surface.data as {
            meta?: {
              composition?: { kind: string; detail: string; code?: string; fixAction?: string };
              assembly?: string;
              progress?: unknown[];
              workspaceTitle?: string;
              workspaceIntent?: string;
            };
          }
        ).meta
      : undefined;
  const assembly = meta?.assembly;
  const blocked = assembly === "model_required" || assembly === "composition_failed";
  const ready = !!surface && assembly === "ready";
  const stageState: StageState = ready ? "ready" : blocked ? "blocked" : "assembling";
  const accent = seatVar(doc.room.people[viewer]?.color);
  const pill = ready
    ? meta?.composition?.kind === "model"
      ? "Agent composed"
      : meta?.composition?.detail?.startsWith("Standard layout")
        ? "Standard layout"
        : "Descriptor preview · no model"
    : assembly === "model_required"
      ? "Model connection needed"
      : assembly === "composition_failed"
        ? "Needs attention"
        : "Assembling · usually ~20 s";
  const notes = (meta?.progress ?? []).filter((n): n is string => typeof n === "string");
  return (
    <div className={s.studio}>
      <AssemblyStage
        title={meta?.workspaceTitle || "Your workspace"}
        intent={meta?.workspaceIntent || "Your agent is making room for the task ahead."}
        pill={pill}
        state={stageState}
        surface={ready ? surface : undefined}
        accent={accent}
        notes={notes}
        message={
          blocked ? (
            <section className={s.assemblyCard}>
              <Mark size={36} loading={false} />
              <p className={s.caps}>Consilith workspace</p>
              <h2>
                {assembly === "model_required"
                  ? "Let your agent take the first step."
                  : "Your model needs attention."}
              </h2>
              <p>
                {assembly === "model_required"
                  ? meta?.composition?.detail && meta.composition.code
                    ? meta.composition.detail
                    : "Connect a model in your agent settings. It will compose a workspace around this task, your inputs and the evidence you need."
                  : meta?.composition?.detail}
              </p>
              {viewer === me ? (
                <div className={s.assemblyActions}>
                  <Link to="/settings/agent" className={s.settingsLink}>
                    {meta?.composition?.fixAction === "reconnect" ||
                    meta?.composition?.fixAction === "reenter_key" ||
                    meta?.composition?.fixAction === "connect"
                      ? "Reconnect your model"
                      : "Open provider settings"}
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
                    {meta?.composition?.code === "quota"
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
          ) : null
        }
      >
        {(stage) =>
          surface ? (
            <A2UISurface
              surface={surface}
              scope={`${doc.room.id}:${sid}`}
              onAction={viewer === me && !composing ? act : undefined}
              loadArtifact={loadArtifact}
              uploadArtifact={actionable ? uploadArtifact : undefined}
              live={live}
              assemble
              accent={accent}
              agent={
                doc.room.people[viewer] ? `${doc.room.people[viewer].name}’s agent` : "Your agent"
              }
              {...stage}
            />
          ) : null
        }
      </AssemblyStage>
      {error ? (
        <p role="alert" className={s.bad}>
          {error}
        </p>
      ) : null}
      <PinnedViews
        doc={doc}
        me={me}
        viewer={viewer}
        surfaces={surfaces}
        live={live}
        loadArtifact={loadArtifact}
        {...(actionable ? { onAction: act, uploadArtifact } : {})}
        {...(onRefreshPin ? { onRefresh: onRefreshPin } : {})}
        {...(onUnpin ? { onUnpin } : {})}
      />
    </div>
  );
}
