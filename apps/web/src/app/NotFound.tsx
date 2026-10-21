import { Link } from "react-router";
import { Mark } from "@/shared/ui";

export function NotFound() {
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
      <div style={{ display: "grid", gap: 14, justifyItems: "center" }}>
        <Mark size={56} label="Consilith" />
        <h1 style={{ margin: 0, fontFamily: "var(--font-display)", fontSize: 32 }}>
          Nothing lives at this address.
        </h1>
        <Link to="/">Go to your rooms</Link>
      </div>
    </main>
  );
}
