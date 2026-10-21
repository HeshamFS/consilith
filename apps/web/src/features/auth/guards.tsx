import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { Mark } from "@/shared/ui";
import { useAuth } from "./AuthProvider";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div
      role="status"
      style={{ minHeight: "100dvh", display: "grid", placeItems: "center", color: "var(--ink)" }}
    >
      <Mark size={56} loading label={label} />
    </div>
  );
}

/** Signed-in screens. A person who has not finished their profile goes to About you first. */
export function RequireAuth({
  children,
  profile = true,
}: {
  children: ReactNode;
  profile?: boolean;
}) {
  const { status, person } = useAuth();
  const loc = useLocation();
  if (status === "loading") return <Loading />;
  if (status === "signed-out" || !person) {
    const next = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/signin?next=${next}`} replace />;
  }
  if (profile && !person.profileComplete) return <Navigate to="/welcome/about" replace />;
  return children;
}

export function safeNext(raw: string | null): string {
  // Only same-app paths: never a protocol, a host or a backslash trick.
  if (!raw?.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return "/";
  return raw;
}
