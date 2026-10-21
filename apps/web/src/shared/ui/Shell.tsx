import type { ReactNode } from "react";
import { Link } from "react-router";
import { ThemeSwitch } from "@/shared/theme/ThemeSwitch";
import { Avatar, Icon } from "./index";
import { Lockup } from "./Mark";
import s from "./shell.module.css";

/** Onboarding: a centred lockup over one card, framed by thin drafting lines. */
export function OnboardingShell({
  step,
  of,
  label,
  wide = false,
  children,
}: {
  /** Omitted outside onboarding (settings): no step counter. */
  step?: number;
  of?: number;
  label: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={s.onboard}>
      <header className={s.onboardTop}>
        <Lockup size={32} />
        <div className={s.topRight}>
          <ThemeSwitch />
        </div>
      </header>
      <main className={`${s.card} ${wide ? s.cardWide : ""}`}>
        <div className={s.stepRow}>
          <span className={s.caps}>{step && of ? `Step ${step} of ${of} · ${label}` : label}</span>
          {step && of ? (
            <span className={s.steps} aria-hidden>
              {Array.from({ length: of }, (_, i) => `step-${i + 1}`).map((k, i) => (
                <span key={k} className={i < step ? s.stepOn : s.stepOff} />
              ))}
            </span>
          ) : null}
        </div>
        {children}
      </main>
    </div>
  );
}

/** Workspace pages (Home, New room): a top bar with the lockup and who you are. */
export function WorkspaceShell({
  person,
  crumbs,
  children,
}: {
  person: { name: string; color: string };
  crumbs?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={s.workspace}>
      <header className={s.bar}>
        <div className={s.barLeft}>
          <Link to="/" className={s.homeLink} aria-label="Your rooms">
            <Lockup size={24} />
          </Link>
          {crumbs ? (
            <nav aria-label="Breadcrumb" className={s.crumbs}>
              {crumbs}
            </nav>
          ) : null}
        </div>
        <div className={s.barRight}>
          <ThemeSwitch />
          <Link
            to="/settings/agent"
            className={s.me}
            aria-label={`${person.name}: your agent settings`}
          >
            <Avatar name={person.name} color={person.color} size={30} />
            <Icon name="agent" size={20} accent={`var(--seat-${person.color})`} />
            <span className={s.meName}>{person.name}</span>
          </Link>
        </div>
      </header>
      <div className={s.workspaceBody}>{children}</div>
    </div>
  );
}

/** An invitation: the lockup and who you are over one wide card. */
export function InviteShell({
  person,
  children,
}: {
  person: { name: string; color: string };
  children: ReactNode;
}) {
  return (
    <div className={s.onboard}>
      <header className={s.inviteTop}>
        <Link to="/" className={s.homeLink} aria-label="Your rooms">
          <Lockup size={32} />
        </Link>
        <div className={s.barRight}>
          <ThemeSwitch />
          <span className={s.me}>
            <Avatar name={person.name} color={person.color} size={30} />
            <Icon name="agent" size={20} accent={`var(--seat-${person.color})`} />
            <span className={s.meName}>{person.name}</span>
          </span>
        </div>
      </header>
      <main className={`${s.card} ${s.cardWide}`}>{children}</main>
    </div>
  );
}
