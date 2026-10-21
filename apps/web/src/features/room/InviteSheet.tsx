import { type FormEvent, useState } from "react";
import { ApiError } from "@/shared/http/client";
import { Button, Icon, TextField } from "@/shared/ui";
import s from "./drawer.module.css";
import { Sheet } from "./Sheet";
import type { RoomConnection } from "./store";

export function InviteSheet({
  conn,
  open,
  onClose,
}: {
  conn: RoomConnection;
  open: number;
  onClose: () => void;
}) {
  const [link, setLink] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(`consilith.roomlink.${conn.room}`);
    } catch {
      return null;
    }
  });
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  async function makeLink() {
    setBusy(true);
    setError(null);
    try {
      const r = await conn.invite();
      setLink(r.url);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "No link could be made.");
    } finally {
      setBusy(false);
    }
  }

  async function sendEmail(e: FormEvent) {
    e.preventDefault();
    const v = email.trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) {
      setError("That is not an email address.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await conn.invite(v);
      setSent((l) => [...l, v]);
      setEmail("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The invite was not sent.");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("Copy is blocked here; select the link and copy it.");
    }
  }

  return (
    <Sheet
      title="Invite to this room"
      sub={
        open
          ? `${open} seat${open === 1 ? "" : "s"} open. Each person picks their own role.`
          : "All four seats are taken."
      }
      icon={<Icon name="invite" size={28} />}
      narrow
      onClose={onClose}
    >
      <div className={s.stack}>
        <form className={s.stack} onSubmit={sendEmail} noValidate>
          <TextField
            label="Invite by email"
            type="email"
            inputMode="email"
            placeholder="colleague@lab.org"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setError(null);
            }}
            hint="A single-use link for that address only."
          />
          <Button type="submit" disabled={busy || !open}>
            Send the invite
          </Button>
        </form>
        {sent.length ? (
          <p className={s.small} role="status">
            Sent to {sent.join(", ")}.
          </p>
        ) : null}

        <h3 className={s.h3}>Or share the room link</h3>
        {link ? (
          <div className={s.current}>
            <Icon name="room" size={20} />
            <div className={s.mono} style={{ overflowWrap: "anywhere" }}>
              {link}
            </div>
            <Button variant="secondary" small onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        ) : (
          <Button variant="secondary" disabled={busy || !open} onClick={makeLink}>
            Make a room link
          </Button>
        )}
        <p className={s.small}>The room link works for anyone for 7 days and can be revoked.</p>
        {error ? (
          <p className={s.bad} role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Sheet>
  );
}
