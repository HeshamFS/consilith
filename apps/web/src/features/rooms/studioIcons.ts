import { CAPABILITIES } from "@/shared/domain/disciplines";
import { studioIcon } from "@/shared/domain/studioIcon";
import type { IconName } from "@/shared/ui";

/** Each studio's own icon (from the plugin it comes from), and the input it starts from. */
export const STUDIO_ICON = new Proxy<Record<string, { icon: IconName; start: IconName }>>(
  {},
  {
    get: (_target, key) => {
      const id = String(key);
      const cap = CAPABILITIES[id];
      const icon = studioIcon({ id, label: cap?.label ?? "", pluginId: cap?.pluginId ?? "" });
      return { icon, start: icon === "microstructure" ? "micrograph" : "formula" };
    },
  },
);
