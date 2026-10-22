import { Children, type CSSProperties, type ReactNode, useState } from "react";
import { gridCols, splitTemplate } from "./planGeometry";
import type { Component } from "./types";
import s from "./workspace-layout.module.css";

type LayoutComponent = Extract<Component, { component: "WorkspaceLayout" }>;
export function Layout({
  component: c,
  children,
}: {
  component: LayoutComponent;
  children: ReactNode;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const active = selected && c.children.includes(selected) ? selected : c.children[0];
  const nodes = Children.toArray(children);
  return (
    <section
      className={s.layout}
      data-kind={c.kind}
      data-ratio={c.ratio}
      data-emphasis={c.emphasis}
      data-density={c.density}
      aria-label={c.title || undefined}
      style={
        {
          ["--tpl" as string]: splitTemplate(c.ratio, c.children.length),
          ["--cols" as string]: gridCols(c.children.length),
        } as CSSProperties
      }
    >
      {c.title ? (
        <header className={s.heading}>
          <span className={s.rule} />
          <div>
            <h2>{c.title}</h2>
            {c.description ? <p>{c.description}</p> : null}
          </div>
        </header>
      ) : null}
      {c.kind === "tabs" ? (
        <>
          <div role="tablist" aria-label={c.title || "Workspace panels"} className={s.tabs}>
            {c.children.map((id, i) => (
              <button
                type="button"
                key={id}
                role="tab"
                id={`${c.id}-${id}-tab`}
                aria-selected={active === id}
                aria-controls={`${c.id}-${id}-panel`}
                onClick={() => setSelected(id)}
              >
                {c.labels[i] || `Panel ${i + 1}`}
              </button>
            ))}
          </div>
          {nodes.map((node, i) => (
            <div
              key={c.children[i]}
              id={`${c.id}-${c.children[i]}-panel`}
              role="tabpanel"
              className={s.tabpanel}
              aria-labelledby={`${c.id}-${c.children[i]}-tab`}
              hidden={c.children[i] !== active}
            >
              {node}
            </div>
          ))}
        </>
      ) : (
        <div className={s.body}>{children}</div>
      )}
    </section>
  );
}
