import type { FieldDef } from "@/shared/domain/disciplines";

/** How a spec field's stored value becomes the text a draft holds, and back. A draft is always
 * text (see `useFieldDraft`), so a switch, a date, a set of choices and an uploaded file are
 * written as text too: "true" or "false", "YYYY-MM-DD", a JSON array, and the artifact's sha256. */

const SHA256 = /^[a-f0-9]{64}$/;
const DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;

/** The kinds that apply as soon as the person picks, with no separate Apply step. */
export const IMMEDIATE = new Set(["choice", "boolean", "date", "multichoice", "artifact"]);

export function artifactSha(value: unknown): string {
  const sha = value && typeof value === "object" ? (value as { artifact?: unknown }).artifact : "";
  return typeof sha === "string" ? sha : "";
}

export function pickedChoices(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

export function displayOf(f: FieldDef, value: unknown): string {
  switch (f.kind) {
    case "object":
    case "list":
      return JSON.stringify(value ?? (f.kind === "list" ? [] : {}), null, 2);
    case "boolean":
      return value === true ? "true" : "false";
    case "multichoice":
      return JSON.stringify(pickedChoices(value));
    case "artifact":
      return artifactSha(value);
    default:
      return String(
        typeof value === "object" && value !== null && "v" in value
          ? (value as { v: unknown }).v
          : (value ?? ""),
      );
  }
}

/** The value to store for the draft text. Throws an Error in plain words when it is not valid
 * for the field; the server checks again. */
export function parseDraft(f: FieldDef, text: string, unit: string, boundsApply: boolean): unknown {
  switch (f.kind) {
    case "number":
    case "integer": {
      if (!text.trim() || !Number.isFinite(Number(text))) throw new Error("Enter a finite number");
      const n = Number(text);
      if (f.kind === "integer" && !Number.isInteger(n)) throw new Error("Enter an integer");
      if (boundsApply && ((f.min != null && n < f.min) || (f.max != null && n > f.max)))
        throw new Error("Value is outside the declared bounds");
      return { v: n, unit };
    }
    case "list":
    case "object": {
      const v: unknown = JSON.parse(text);
      if (f.kind === "list" && !Array.isArray(v)) throw new Error("Expected a JSON array");
      if (f.kind === "object" && (!v || typeof v !== "object" || Array.isArray(v)))
        throw new Error("Expected a JSON object");
      return v;
    }
    case "choice":
      if (!(f.choices ?? []).includes(text)) throw new Error("Choose one of the declared options");
      return text;
    case "boolean":
      if (text !== "true" && text !== "false") throw new Error("Choose on or off");
      return text === "true";
    case "date": {
      const real = DATE.test(text) && !Number.isNaN(Date.parse(`${text}T00:00:00Z`));
      const back = real ? new Date(`${text}T00:00:00Z`).toISOString().slice(0, 10) : "";
      if (back !== text) throw new Error("Enter a real date");
      return text;
    }
    case "multichoice": {
      const v: unknown = JSON.parse(text);
      const picked = pickedChoices(v);
      if (!Array.isArray(v) || picked.length !== v.length || new Set(picked).size !== picked.length)
        throw new Error("Choose from the declared options");
      if (picked.some((p) => !(f.choices ?? []).includes(p)))
        throw new Error("Choose from the declared options");
      return picked;
    }
    case "artifact":
      if (!SHA256.test(text)) throw new Error("The stored file reference is not valid");
      return { artifact: text };
    default:
      return text;
  }
}
