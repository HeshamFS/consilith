import type { IconName } from "@/shared/ui";
/** Generic shell icon. Specialized pictures belong to registered views. */
export const STUDIO_ICON = new Proxy<Record<string, { icon: IconName; start: IconName }>>(
  {},
  { get: () => ({ icon: "solvers", start: "formula" }) },
);
