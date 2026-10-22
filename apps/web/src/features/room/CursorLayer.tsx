import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from "react";
import { prefersReducedMotion } from "@/shared/a2ui/Assembly";
import { Icon, seatVar } from "@/shared/ui";
import c from "./cursor.module.css";
import type { Presence, RoomDoc } from "./types";

/** Live cursors on the room canvas.
 *
 * Agents: the room sends an agent's presence when one of its tool calls goes ahead
 * (agents/cursor.py), naming what the call acts on (`focus`) and the action in words. The layer
 * finds that element in the studio (a field, a lane form, the run control, the workspace), glides
 * the agent's cursor there in its owner's seat colour with an "Ankita’s agent" pill, and lets it
 * fade after a few quiet seconds. A target above or below the visible canvas pins the cursor to
 * that edge.
 *
 * People: each person's pointer, in canvas content pixels, is sent about 15 times a second
 * (`onMove`) and drawn for everyone else in the same studio. Your own pointer is never drawn. */

export const AGENT_LINGER_MS = 6000;
export const PERSON_LINGER_MS = 10000;
const FADE_MS = 600;
const EDGE = 8;
const TICK_MS = 500;

type Placed = {
  key: string;
  name: string;
  color: string;
  agent: boolean;
  action?: string | undefined;
  x: number;
  y: number;
  edge?: "up" | "down" | undefined;
  faded: boolean;
};

const quote = (s: string) => s.replace(/["\\]/g, "\\$&");

/** The element an agent's focus names inside `root`, or null when it is not on the canvas. */
export function focusTarget(root: ParentNode, focus: string): Element | null {
  if (focus.startsWith("/twin/")) {
    const parts = focus.split("/"); // ["", "twin", case, lane, field?]
    if (parts.length >= 5) {
      const field = root.querySelector(`[data-field="${quote(focus)}"]`);
      if (field) return field;
    }
    const lane = parts.slice(0, 4).join("/");
    return (
      root.querySelector(`[data-lane="${quote(lane)}"]`) ??
      root.querySelector(`[data-field^="${quote(`${lane}/`)}"]`)
    );
  }
  if (focus.startsWith("run:"))
    return root.querySelector(`[data-run-case="${quote(focus.slice(4))}"]`);
  if (focus.startsWith("workspace:")) return root.querySelector('section[aria-label="Workspace"]');
  return null;
}

function firstName(doc: RoomDoc, id: string): string {
  return doc.room.people[id]?.name ?? id;
}

export function CursorLayer({
  doc,
  me,
  caseId,
  active,
  presence,
  onMove,
}: {
  doc: RoomDoc;
  me: string;
  caseId: string;
  /** A studio is on the canvas: only then are pointers sent and cursors drawn. */
  active: boolean;
  presence: Record<string, Presence>;
  onMove?: ((p: { cursor: number[]; case: string }) => void) | undefined;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [reduced] = useState(prefersReducedMotion);
  // When each presence last changed, by this browser's clock (the server's may differ).
  const [arrivals, setArrivals] = useState<Map<string, { p: Presence; at: number }>>(
    () => new Map(),
  );
  const [now, setNow] = useState(() => Date.now());
  const [tick, setTick] = useState(0);
  const [placed, setPlaced] = useState<Placed[]>([]);

  useEffect(() => {
    setArrivals((prev) => {
      let changed = false;
      const next = new Map(prev);
      const t = Date.now();
      for (const p of Object.values(presence)) {
        if (prev.get(p.person)?.p !== p) {
          next.set(p.person, { p, at: t });
          changed = true;
        }
      }
      if (changed) setNow(t);
      return changed ? next : prev;
    });
  }, [presence]);

  // A clock for fading, only while something is shown.
  const showing = placed.length > 0;
  useEffect(() => {
    if (!showing) return;
    const t = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(t);
  }, [showing]);

  // Send my pointer; place again when the canvas scrolls or resizes.
  useEffect(() => {
    const canvas = ref.current?.parentElement;
    if (!canvas || !active) return;
    const move = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect();
      const x = Math.round(e.clientX - r.left + canvas.scrollLeft);
      const y = Math.round(e.clientY - r.top + canvas.scrollTop);
      onMove?.({ cursor: [x, y], case: caseId });
    };
    const again = () => setTick((n) => n + 1);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("scroll", again, { passive: true });
    window.addEventListener("resize", again);
    return () => {
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("scroll", again);
      window.removeEventListener("resize", again);
    };
  }, [active, caseId, onMove]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `tick` re-places after a scroll
  useLayoutEffect(() => {
    const canvas = ref.current?.parentElement;
    if (!canvas || !active) {
      setPlaced((p) => (p.length ? [] : p));
      return;
    }
    const box = canvas.getBoundingClientRect();
    const top = canvas.scrollTop;
    const bottom = top + canvas.clientHeight;
    const out: Placed[] = [];
    for (const { p, at } of arrivals.values()) {
      const age = now - at;
      if (p.agent) {
        if (!p.focus || !p.owner || (p.case && p.case !== caseId)) continue;
        if (age >= AGENT_LINGER_MS + FADE_MS) continue;
        const el = focusTarget(canvas, p.focus);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        let x = r.left - box.left + canvas.scrollLeft + Math.min(28, r.width / 2);
        let y = r.top - box.top + top + Math.min(20, r.height / 2);
        let edge: Placed["edge"];
        if (y < top + EDGE) {
          y = top + EDGE;
          edge = "up";
        } else if (bottom > top && y > bottom - 48) {
          y = bottom - 48;
          edge = "down";
        }
        x = Math.max(EDGE, x);
        out.push({
          key: p.person,
          name: `${firstName(doc, p.owner)}’s agent`,
          color: seatVar(doc.room.people[p.owner]?.color),
          agent: true,
          action: p.action,
          x,
          y,
          edge,
          faded: age >= AGENT_LINGER_MS,
        });
      } else {
        if (p.person === me || !p.cursor || p.cursor.length < 2) continue;
        if (p.case && p.case !== caseId) continue;
        if (age >= PERSON_LINGER_MS + FADE_MS) continue;
        out.push({
          key: p.person,
          name: firstName(doc, p.person),
          color: seatVar(doc.room.people[p.person]?.color),
          agent: false,
          x: p.cursor[0] ?? 0,
          y: p.cursor[1] ?? 0,
          faded: age >= PERSON_LINGER_MS,
        });
      }
    }
    setPlaced((prev) => (JSON.stringify(prev) === JSON.stringify(out) ? prev : out));
  }, [arrivals, now, tick, caseId, active, me, doc]);

  return (
    <div
      ref={ref}
      className={c.layer}
      data-reduced={reduced ? "true" : undefined}
      aria-hidden
      data-testid="cursor-layer"
    >
      {placed.map((p) => (
        <div
          key={p.key}
          className={c.cursor}
          data-testid="cursor"
          data-agent={p.agent ? "true" : undefined}
          data-person={p.key}
          data-edge={p.edge}
          data-faded={p.faded ? "true" : undefined}
          style={
            {
              transform: `translate(${p.x}px, ${p.y}px)`,
              "--cursor-color": p.color,
            } as CSSProperties
          }
        >
          <div className={c.head}>
            {/* The design canvas cursor (board 11): a 20 px arrow in the seat colour with a dark
                outline, and a round name pill beside it. */}
            <svg className={c.arrow} width="20" height="20" viewBox="0 0 24 24" aria-hidden>
              <path d="M4 3l16 7-7 2-3 7z" />
            </svg>
            <span className={c.pill}>
              {p.agent ? <Icon name="agent" size={13} /> : null}
              {p.name}
              {p.edge ? <span className={c.edge}>{p.edge === "up" ? "↑" : "↓"}</span> : null}
            </span>
          </div>
          {p.action ? <span className={c.action}>{p.action}</span> : null}
        </div>
      ))}
    </div>
  );
}
