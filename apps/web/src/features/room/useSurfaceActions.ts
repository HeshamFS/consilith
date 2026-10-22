import { useCallback } from "react";
import type { A2UIAction } from "@/shared/a2ui/Renderer";
import { rawFetch } from "@/shared/http/client";
import type { RoomConnection } from "./store";

type Conn = Pick<RoomConnection, "decide" | "editTwin" | "solve">;

/** What a surface needs to act on the room: the person's edits, decisions and runs, and the
 * download of a stored artifact. These are the person's own actions, so they do not depend on
 * what the person's agent may do. Shared by the studio and by surfaces in chat.
 *
 * `onDone` runs after a successful action (the studio refreshes its surface there). `viewer` is
 * whose surface this is: acting is refused while looking at somebody else's. */
export function useSurfaceActions({
  conn,
  roomId,
  me,
  viewer,
  onDone,
}: {
  conn: Conn;
  roomId: string;
  me: string;
  viewer: string;
  onDone?: () => Promise<unknown>;
}) {
  const loadArtifact = useCallback(
    async (sha: string) => {
      if (!/^[a-f0-9]{64}$/.test(sha)) throw new Error("Invalid artifact reference");
      const response = await rawFetch(`/rooms/${encodeURIComponent(roomId)}/artifacts/${sha}`);
      if (!response.ok) throw new Error("Artifact could not be loaded");
      return response.blob();
    },
    [roomId],
  );
  const act = useCallback(
    async (action: A2UIAction) => {
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
      await onDone?.();
    },
    [conn, me, viewer, onDone],
  );
  return { act, loadArtifact };
}
