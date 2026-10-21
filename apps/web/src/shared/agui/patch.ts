/** RFC 6902 JSON Patch, applied atomically: a failed operation leaves the input untouched, so
 * the caller can skip a STATE_DELTA that does not apply (as AG-UI requires). */

export type PatchOp = {
  op: "add" | "replace" | "remove" | "test" | "move" | "copy";
  path: string;
  value?: unknown;
  from?: string;
};

type Json = unknown;
type Obj = Record<string, Json>;

// Keys that would reach an object's prototype: a patch using one fails as a whole.
const UNSAFE = new Set(["__proto__", "prototype", "constructor"]);

function parse(pointer: string): string[] {
  if (pointer === "") return [];
  if (!pointer.startsWith("/")) throw new Error(`bad pointer ${pointer}`);
  const keys = pointer
    .slice(1)
    .split("/")
    .map((t) => t.replace(/~1/g, "/").replace(/~0/g, "~"));
  if (keys.some((k) => UNSAFE.has(k))) throw new Error("unsafe key");
  return keys;
}

// RFC 6901 array index: "0" or digits without a leading zero.
function index(k: string, length: number, allowEnd: boolean): number {
  if (allowEnd && k === "-") return length;
  if (!/^(0|[1-9][0-9]*)$/.test(k)) throw new Error("bad index");
  const i = Number(k);
  if (i > length || (!allowEnd && i >= length)) throw new Error("bad index");
  return i;
}

function equal(a: Json, b: Json): boolean {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => equal(v, b[i]));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    return ka.every((k) => Object.hasOwn(b, k) && equal((a as Obj)[k], (b as Obj)[k]));
  }
  return false;
}

function get(doc: Json, keys: string[]): Json {
  let cur: Json = doc;
  for (const k of keys) {
    if (Array.isArray(cur)) cur = cur[index(k, cur.length, false)];
    else if (cur && typeof cur === "object" && Object.hasOwn(cur, k)) cur = (cur as Obj)[k];
    else throw new Error("path not found");
    if (cur === undefined) throw new Error("path not found");
  }
  return cur;
}

function put(doc: Json, keys: string[], value: Json, mode: "add" | "replace"): Json {
  if (!keys.length) return value;
  const parent = get(doc, keys.slice(0, -1));
  const k = keys[keys.length - 1] as string;
  if (Array.isArray(parent)) {
    if (mode === "add") parent.splice(index(k, parent.length, true), 0, value);
    else parent[index(k, parent.length, false)] = value;
  } else if (parent && typeof parent === "object") {
    if (mode === "replace" && !Object.hasOwn(parent, k)) throw new Error("path not found");
    (parent as Obj)[k] = value;
  } else throw new Error("parent is not a container");
  return doc;
}

function del(doc: Json, keys: string[]): Json {
  if (!keys.length) throw new Error("cannot remove the root");
  const parent = get(doc, keys.slice(0, -1));
  const k = keys[keys.length - 1] as string;
  if (Array.isArray(parent)) {
    parent.splice(index(k, parent.length, false), 1);
  } else if (parent && typeof parent === "object" && Object.hasOwn(parent, k)) {
    delete (parent as Obj)[k];
  } else throw new Error("path not found");
  return doc;
}

export function applyPatch<T>(
  doc: T,
  ops: PatchOp[],
): { ok: true; doc: T } | { ok: false; error: string } {
  let out: Json = structuredClone(doc);
  try {
    for (const op of ops) {
      const keys = parse(op.path);
      switch (op.op) {
        case "add":
          out = put(out, keys, structuredClone(op.value), "add");
          break;
        case "replace":
          out = put(out, keys, structuredClone(op.value), "replace");
          break;
        case "remove":
          out = del(out, keys);
          break;
        case "copy":
          out = put(out, keys, structuredClone(get(out, parse(op.from ?? ""))), "add");
          break;
        case "move": {
          const from = parse(op.from ?? "");
          const v = get(out, from);
          out = del(out, from);
          out = put(out, keys, v, "add");
          break;
        }
        case "test":
          if (!equal(get(out, keys), op.value)) throw new Error("test failed");
          break;
        default:
          throw new Error("unknown op");
      }
    }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "patch failed" };
  }
  return { ok: true, doc: out as T };
}
