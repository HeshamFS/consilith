import { type FormEvent, useEffect, useRef, useState } from "react";
import { Button, Icon, seatVar } from "@/shared/ui";
import { useAgentSession } from "./agentSession";
import s from "./chat.module.css";
import { Sheet } from "./Sheet";

/** Talk to your personal agent over its AG-UI session, and answer its approvals there. The
 * agent reads, labels and negotiates; numbers come only from solvers, never from this chat. */
export function AgentChat({
  room,
  person,
  agentName,
  onClose,
}: {
  room: string;
  person: { id: string; name: string; color: string };
  agentName: string;
  onClose: () => void;
}) {
  const a = useAgentSession(room, person.id);
  const [text, setText] = useState("");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [pick, setPick] = useState<Record<string, boolean | "later">>({});
  const end = useRef<HTMLDivElement>(null);
  const c = seatVar(person.color);

  const count = a.messages.length;
  useEffect(() => {
    if (count) end.current?.scrollIntoView?.({ block: "end" });
  }, [count]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || a.running) return;
    setText("");
    void a.send(t);
  }

  const ready = a.interrupts.every((i) => {
    const p = pick[i.id];
    return p === "later" || (typeof p === "boolean" && (reasons[i.id] ?? "").trim().length > 0);
  });

  return (
    <Sheet
      title={agentName}
      sub="Your agent's session in this room. It never produces a number; solvers do."
      icon={<Icon name="agent" size={30} accent={c} />}
      narrow
      onClose={onClose}
    >
      <div className={s.wrap}>
        {a.interrupts.length ? (
          <section className={s.approvals} aria-label="Waiting for your decision">
            <h3 className={s.h3}>Waiting for your decision · {a.interrupts.length}</h3>
            {a.interrupts.map((i) => (
              <div key={i.id} className={s.approval}>
                <p className={s.msg}>{i.message}</p>
                <div className={s.choices}>
                  {(
                    [
                      [true, "Accept"],
                      [false, "Decline"],
                      ["later", "Later"],
                    ] as const
                  ).map(([v, label]) => (
                    <button
                      key={label}
                      type="button"
                      className={s.choice}
                      aria-pressed={pick[i.id] === v}
                      onClick={() => setPick((p) => ({ ...p, [i.id]: v }))}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                {typeof pick[i.id] === "boolean" ? (
                  <label className={s.reason}>
                    <span className="visually-hidden">Reason</span>
                    <input
                      value={reasons[i.id] ?? ""}
                      placeholder="Your reason (it becomes a lesson)"
                      onChange={(e) => setReasons((r) => ({ ...r, [i.id]: e.target.value }))}
                    />
                  </label>
                ) : null}
              </div>
            ))}
            <Button
              disabled={!ready || a.running}
              onClick={() =>
                a.answer(
                  Object.fromEntries(
                    a.interrupts.map((i) => {
                      const p = pick[i.id];
                      return [
                        i.id,
                        p === "later" || p === undefined
                          ? "later"
                          : { approved: p, reason: (reasons[i.id] ?? "").trim() },
                      ];
                    }),
                  ),
                )
              }
            >
              Send my decisions
            </Button>
            <p className={s.small}>Every open approval needs an answer; Later keeps it open.</p>
          </section>
        ) : null}

        <div className={s.log} aria-live="polite">
          {a.messages.length ? (
            a.messages.map((m) => (
              <div
                key={m.id}
                className={m.role === "user" ? s.me : s.agent}
                style={m.role === "user" ? { background: c } : undefined}
              >
                {m.content || (a.running ? "…" : "")}
              </div>
            ))
          ) : (
            <p className={s.small}>
              {a.running
                ? "Connecting to your agent…"
                : "Ask what it is watching, why it proposed something, or what to try next."}
            </p>
          )}
          <div ref={end} />
        </div>

        {a.error ? (
          <p className={s.bad} role="alert">
            {a.error}
          </p>
        ) : null}

        <form className={s.composer} onSubmit={submit}>
          <label className="visually-hidden" htmlFor="agent-say">
            Message your agent
          </label>
          <input
            id="agent-say"
            value={text}
            maxLength={2000}
            placeholder={
              a.interrupts.length ? "Answer the approvals first" : `Message ${agentName}`
            }
            disabled={a.interrupts.length > 0}
            onChange={(e) => setText(e.target.value)}
          />
          <Button type="submit" disabled={!text.trim() || a.running || a.interrupts.length > 0}>
            Send
          </Button>
        </form>
      </div>
    </Sheet>
  );
}
