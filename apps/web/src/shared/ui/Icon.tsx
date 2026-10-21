import type { CSSProperties } from "react";
import { ICONS, type IconName } from "./icons.generated";

export type { IconName };

type Props = {
  name: IconName;
  size?: number;
  /** Owner colour for the agent's antenna petal and a lane's owner dot. */
  accent?: string;
  /** When set, the icon is announced with this label; otherwise it is decorative. */
  label?: string;
  className?: string;
};

/** The approved Consilith icon set (scripts/icon_set.py). Ink follows currentColor. */
export function Icon({ name, size = 20, accent, label, className }: Props) {
  const icon = ICONS[name];
  const style = accent ? ({ "--icon-accent": accent } as CSSProperties) : undefined;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      style={{ flex: "none", ...style }}
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
      // biome-ignore lint/security/noDangerouslySetInnerHtml: static, generated from the repo's own icon set
      dangerouslySetInnerHTML={{ __html: icon.body }}
    />
  );
}
