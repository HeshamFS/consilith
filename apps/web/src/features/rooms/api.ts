import type { Capability, StudioId } from "@/shared/domain/disciplines";
import { api } from "@/shared/http/client";

export type SeatView = { id: string; name: string; color: string };
export type RoomSummary = {
  id: string;
  title: string;
  studio: StudioId;
  seats: SeatView[];
  role: string | null;
  /** True when this row is a pending email invite rather than a seat. */
  waiting: boolean;
  /** Who created the room: only they can delete it, and they cannot leave it. */
  owner?: string | null;
};
export type InviteView = {
  room: { id: string; title: string; studio: StudioId; capabilities?: Capability[] };
  inviter: SeatView;
  seats: (SeatView & { role?: string })[];
  /** The room's lane owners per studio, as in the room document. */
  lanes: Partial<Record<StudioId, Record<string, string | null>>>;
  expiresAt: string;
  seated: boolean;
  full: boolean;
};

export const roomsApi = {
  list: () => api.get<RoomSummary[]>("/rooms"),
  create: (r: {
    id: string;
    title: string;
    studio: StudioId;
    role: string;
    invite: string[];
    alone?: boolean;
  }) => api.post<{ id: string; seq: number; link: string }>("/rooms", r),
  invite: (room: string, email?: string) =>
    api.post<{ id: string; url: string; expiresAt: string }>(
      `/rooms/${encodeURIComponent(room)}/invites`,
      email ? { email } : {},
    ),
  leave: (room: string) => api.post<{ left: string }>(`/rooms/${encodeURIComponent(room)}/leave`),
  remove: (room: string) =>
    api.del<void>(`/rooms/${encodeURIComponent(room)}?confirm=${encodeURIComponent(room)}`),
  invitation: (token: string) => api.get<InviteView>(`/invites/${encodeURIComponent(token)}`),
  accept: (
    token: string,
    body: { role: string; agent: { kind: "consilith" | "brought"; cardUrl?: string } },
  ) =>
    api.post<{ room: string; seq: number; agentError?: string }>(
      `/invites/${encodeURIComponent(token)}/accept`,
      body,
    ),
};

/** The token from a pasted invite link (…/invite/<token>), or the token itself. */
export function inviteToken(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  const m = v.match(/\/(?:invite|join)\/([A-Za-z0-9_-]{8,})\/?(?:[?#].*)?$/);
  if (m?.[1]) return m[1];
  return /^[A-Za-z0-9_-]{8,}$/.test(v) ? v : null;
}

/** A room address from its title: lowercase letters, digits and dashes. */
export function slugify(title: string): string {
  return title
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .slice(0, 40)
    .replace(/^-|-$/g, "");
}
