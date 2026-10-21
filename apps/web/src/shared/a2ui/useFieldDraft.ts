import { useEffect, useState } from "react";

type Draft = { text: string; base: string };
const drafts = new Map<string, Draft>();
/** Draft identity is the room/viewer plus canonical input path, never a model node id. */
export function useFieldDraft(key: string, current: unknown, display: string) {
  const serialized = JSON.stringify(current) ?? "null";
  const [text, setText] = useState(() => drafts.get(key)?.text ?? display);
  const [conflict, setConflict] = useState(false);
  useEffect(() => {
    const saved = drafts.get(key);
    if (saved) {
      setText(saved.text);
      setConflict(saved.base !== serialized);
    } else {
      setText(display);
      setConflict(false);
    }
  }, [key, serialized, display]);
  return {
    text,
    conflict,
    dirty: drafts.has(key),
    baseValue: drafts.has(key) ? JSON.parse(drafts.get(key)?.base ?? "null") : current,
    change(next: string) {
      drafts.set(key, { text: next, base: drafts.get(key)?.base ?? serialized ?? "null" });
      setText(next);
    },
    committed() {
      drafts.delete(key);
      setConflict(false);
    },
    reset() {
      drafts.delete(key);
      setText(display);
      setConflict(false);
    },
  };
}
