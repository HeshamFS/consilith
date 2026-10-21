import { isRouteErrorResponse, Link, useRouteError } from "react-router";
import { Mark } from "@/shared/ui";

/** Shown instead of the router's developer page when a screen fails to render. */
export function RouteError() {
  const err = useRouteError();
  const detail = isRouteErrorResponse(err)
    ? `${err.status} ${err.statusText}`
    : err instanceof Error
      ? err.message
      : "Unknown error";
  return (
    <main
      style={{
        minHeight: "100dvh",
        display: "grid",
        placeItems: "center",
        padding: 24,
        textAlign: "center",
      }}
    >
      <div style={{ display: "grid", gap: 14, justifyItems: "center", maxWidth: 520 }}>
        <Mark size={56} label="Consilith" />
        <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontSize: 30 }}>
          This screen hit a problem.
        </h1>
        <p style={{ margin: 0, color: "var(--ink-2)" }}>
          Nothing in your rooms changed. Reload, or go back to your rooms.
        </p>
        <code style={{ fontSize: 12, color: "var(--ink-3)", overflowWrap: "anywhere" }}>
          {detail}
        </code>
        <Link to="/">Go to your rooms</Link>
      </div>
    </main>
  );
}
