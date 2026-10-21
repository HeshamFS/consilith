/** A wildcard accepts the browser's concrete type; fixed package media types stay authoritative. */
export function intakeMediaType(declared: string | undefined, actual: string): string {
  const accepted = declared || "application/octet-stream";
  if (accepted.endsWith("/*")) {
    if (!actual.startsWith(accepted.slice(0, -1)) || actual.includes("*"))
      throw new Error(`Choose a file with a declared ${accepted} media type`);
    return actual;
  }
  return accepted === "application/octet-stream" ? actual || accepted : accepted;
}
