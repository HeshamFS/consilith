import type { IconName } from "@/shared/ui";

/** A studio's icon from the icon library, by what its plugin is about: a grain structure for
 * microstructure, a lattice for crystals, a part for mechanics; else the solvers mark. One rule
 * for the rail, the room bar, the rooms list, the new-room page and the plugin cards. */
const STUDIO_ICONS: [RegExp, IconName][] = [
  [/micro|grain|micrograph|phase.?field/i, "microstructure"],
  [/crystal|lattice|polyhedr|\bcif\b/i, "crystals"],
  [/mechanic|beam|stress|\bfem\b|\bpart\b/i, "mechanics"],
  [/sketch|draw/i, "sketch"],
  [/formula|composition/i, "formula"],
];

export function studioIcon(studio: { id: string; label?: string; pluginId?: string }): IconName {
  const text = `${studio.pluginId ?? ""} ${studio.id} ${studio.label ?? ""}`;
  return STUDIO_ICONS.find(([re]) => re.test(text))?.[1] ?? "solvers";
}
