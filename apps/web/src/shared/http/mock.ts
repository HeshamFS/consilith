/** Dev-only, explicit mock of docs/web-api.md, for building screens before the room server has
 * M9. Enabled only by VITE_API_MOCK=1 in `vite dev`; a production build refuses it
 * (vite.config.ts) and `mockEnabled()` is false outside dev. It never pretends to be the server
 * silently: the app shows a "mock API" badge whenever it is on. */

type Json = Record<string, unknown>;
type Person = {
  id: string;
  email: string;
  name: string;
  color: string;
  discipline: string;
  profileComplete: boolean;
};
type Room = {
  id: string;
  title: string;
  studio: string;
  seats: { id: string; role: string }[];
  invites: { id: string; token: string; email: string | null }[];
};

/** The server's provider registry (`GET /providers`), as the mock mode serves it. */
const MOCK_PROVIDERS = [
  {
    id: "scripted",
    label: "No model",
    auth: "none",
    needsModel: false,
    dataTerms: "Nothing leaves this host: replies come from built-in rules.",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    auth: "key",
    needsModel: true,
    defaultModel: "claude-sonnet-5-5",
    dataTerms: "Your messages and room context are sent to Anthropic with your API key.",
  },
  {
    id: "openai",
    label: "OpenAI",
    auth: "key",
    needsModel: true,
    dataTerms: "Your messages and room context are sent to OpenAI with your API key.",
  },
  {
    id: "gemini",
    label: "Google Gemini",
    auth: "key",
    needsModel: true,
    dataTerms: "Your messages and room context are sent to Google with your API key.",
  },
  {
    id: "siwc",
    label: "Your ChatGPT plan",
    auth: "chatgpt",
    needsModel: true,
    dataTerms: "Your messages and room context are sent to OpenAI through your own ChatGPT plan.",
  },
];

const db = {
  people: new Map<string, Person>(),
  links: new Map<string, string>(),
  sessions: new Map<string, string>(),
  agents: new Map<string, Json>(),
  rooms: new Map<string, Room>(),
  mailbox: [] as { to: string; url: string; at: string }[],
};

// The mock keeps its state in this tab's sessionStorage, so a full page load (opening a sign-in
// link) behaves like the server, which outlives the page.
const STORE = "consilith.mock";
const MAPS = ["people", "links", "sessions", "agents", "rooms"] as const;

function load() {
  try {
    const raw = sessionStorage.getItem(STORE);
    if (!raw) return;
    const saved = JSON.parse(raw) as Record<string, unknown>;
    for (const k of MAPS) {
      const entries = saved[k];
      if (Array.isArray(entries)) {
        const m = db[k] as Map<string, unknown>;
        for (const [key, v] of entries as [string, unknown][]) m.set(key, v);
      }
    }
    if (Array.isArray(saved.mailbox)) db.mailbox = saved.mailbox as typeof db.mailbox;
  } catch {
    /* start empty */
  }
}

function save() {
  try {
    const out: Record<string, unknown> = { mailbox: db.mailbox };
    for (const k of MAPS) out[k] = [...(db[k] as Map<string, unknown>).entries()];
    sessionStorage.setItem(STORE, JSON.stringify(out));
  } catch {
    /* in memory only */
  }
}

load();

export function mockEnabled(): boolean {
  return import.meta.env.DEV && import.meta.env.VITE_API_MOCK === "1";
}

function rand(): string {
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function err(status: number, code: string, message: string): Response {
  return json(status, { error: { code, message } });
}

function idFor(email: string): string {
  const base =
    (email.split("@")[0] ?? "person")
      .split(/[.+_]/)[0]
      ?.toLowerCase()
      .replace(/[^a-z0-9]/g, "") || "person";
  let id = base;
  for (let n = 2; [...db.people.values()].some((p) => p.id === id && p.email !== email); n++)
    id = `${base}-${n}`;
  return id;
}

function who(init: RequestInit): Person | null {
  const h = new Headers(init.headers).get("Authorization") ?? "";
  const pid = db.sessions.get(h.replace("Bearer ", ""));
  return pid ? (db.people.get(pid) ?? null) : null;
}

function issue(p: Person) {
  const token = rand();
  db.sessions.set(token, p.id);
  return { token, sid: rand().slice(0, 12), person: p, needsProfile: !p.profileComplete };
}

const LANES: Record<string, [string, string][]> = {
  mechanics: [
    ["geometry", "design"],
    ["loads", "design"],
    ["material", "materials"],
    ["fatigue", "materials"],
  ],
  microstructure: [
    ["model", "design"],
    ["solver", "design"],
    ["micrographs", "materials"],
    ["segmentation", "materials"],
    ["anneal", "materials"],
  ],
};

function lanesOf(r: Room) {
  const owners: Record<string, string | null> = {};
  for (const [id, role] of LANES[r.studio] ?? []) {
    owners[id] = r.seats.find((s) => s.role === role)?.id ?? null;
  }
  return { [r.studio]: owners };
}

function roomView(r: Room, me?: Person) {
  return {
    role: r.seats.find((s) => s.id === me?.id)?.role ?? null,
    id: r.id,
    title: r.title,
    studio: r.studio,
    seats: r.seats.map((s) => {
      const p = db.people.get(s.id);
      return { id: s.id, name: p?.name ?? s.id, color: p?.color ?? "blue" };
    }),
    waiting: false,
  };
}

export async function mockFetch(url: string, init: RequestInit): Promise<Response> {
  await new Promise((r) => setTimeout(r, 120));
  try {
    return route(url, init);
  } finally {
    save();
  }
}

function route(url: string, init: RequestInit): Response {
  const u = new URL(url, location.origin);
  const path = u.pathname.replace(/^\/api/, "");
  if (path === "/plugins")
    return json(200, {
      plugins: [
        {
          id: "fixture",
          title: "Development fixture",
          description: "Explicit mock data",
          version: "1",
          capabilities: Object.keys(LANES),
          roles: [
            { id: "design", label: "Model and workflow", autonomy: "run_solvers" },
            { id: "materials", label: "Evidence and review", autonomy: "edit_my_lanes" },
          ],
          tools: [],
          skills: [],
          knowledge: [],
        },
      ],
    });
  if (path === "/capabilities")
    return json(200, {
      capabilities: Object.entries(LANES).map(([id, lanes]) => ({
        id,
        label: id.charAt(0).toUpperCase() + id.slice(1),
        grammar: "Development fixture only",
        inputs: [],
        lanes: lanes.map(([name, role]) => ({
          id: name,
          label: name,
          default_role: role,
          fields: [],
        })),
      })),
    });

  const method = (init.method ?? "GET").toUpperCase();
  const body: Json = init.body ? (JSON.parse(String(init.body)) as Json) : {};
  const me = who(init);

  if (path === "/auth/providers")
    return json(200, { email: true, github: false, devMailbox: true });
  if (path === "/providers" && method === "GET") return json(200, { providers: MOCK_PROVIDERS });
  if (path === "/auth/email" && method === "POST") {
    const email = String(body.email ?? "")
      .trim()
      .toLowerCase();
    if (email.includes("@")) {
      const t = rand();
      db.links.set(t, email);
      db.mailbox.unshift({
        to: email,
        url: `${location.origin}/auth/verify?token=${t}`,
        at: new Date().toISOString(),
      });
    }
    return json(202, { sent: true });
  }
  if (path === "/dev/mailbox") return json(200, db.mailbox);
  if (path === "/auth/email/redeem" && method === "POST") {
    const email = db.links.get(String(body.token));
    if (!email)
      return err(400, "invalid_link", "This sign-in link has expired or was already used.");
    db.links.delete(String(body.token));
    let p = [...db.people.values()].find((x) => x.email === email);
    if (!p) {
      p = {
        id: idFor(email),
        email,
        name: "",
        color: "blue",
        discipline: "none",
        profileComplete: false,
      };
      db.people.set(p.id, p);
    }
    return json(200, issue(p));
  }
  if (!me) return err(401, "unauthorized", "Sign in first.");
  if (path === "/auth/logout") return json(204);
  if (path === "/me" && method === "GET") return json(200, { person: me, sid: "mock" });
  if (path === "/me" && method === "PUT") {
    Object.assign(me, {
      name: String(body.name),
      color: String(body.color),
      discipline: String(body.discipline),
      profileComplete: true,
    });
    return json(200, { person: me });
  }
  if (path === "/me/agent" && method === "GET") {
    const a = db.agents.get(me.id) ?? {
      kind: "consilith",
      provider: "anthropic",
      model: null,
      autonomy: me.discipline === "materials" ? "edit_my_lanes" : "run_solvers",
      cardUrl: null,
      hasKey: false,
    };
    return json(200, a);
  }
  if (path === "/me/agent" && method === "PUT") {
    const prev = db.agents.get(me.id) ?? {};
    const a = { ...prev, ...body, hasKey: Boolean(prev.hasKey) };
    db.agents.set(me.id, a);
    return json(200, a);
  }
  if (path === "/me/agent/key") {
    const prev = db.agents.get(me.id) ?? {};
    db.agents.set(me.id, { ...prev, hasKey: method === "PUT" });
    return json(204);
  }
  if (path === "/rooms" && method === "GET") {
    return json(
      200,
      [...db.rooms.values()]
        .filter((r) => r.seats.some((s) => s.id === me.id))
        .map((r) => roomView(r, me)),
    );
  }
  if (path === "/rooms" && method === "POST") {
    const id = String(body.id);
    if (!/^[a-z0-9-]{2,40}$/.test(id))
      return err(400, "invalid", "Room addresses are lowercase letters, digits and dashes.");
    if (db.rooms.has(id)) return err(409, "taken", "That room address is taken.");
    const r: Room = {
      id,
      title: String(body.title),
      studio: String(body.studio),
      seats: [{ id: me.id, role: String(body.role) }],
      invites: [],
    };
    const link = rand();
    r.invites.push({ id: rand().slice(0, 8), token: link, email: null });
    for (const e of (body.invite as string[] | undefined) ?? []) {
      const t = rand();
      r.invites.push({ id: rand().slice(0, 8), token: t, email: e });
      db.mailbox.unshift({
        to: e,
        url: `${location.origin}/invite/${t}`,
        at: new Date().toISOString(),
      });
    }
    db.rooms.set(id, r);
    return json(201, { id, seq: 1, link: `${location.origin}/invite/${link}` });
  }
  const mk = path.match(/^\/rooms\/([a-z0-9-]+)\/invites$/);
  if (mk && method === "POST") {
    const r = db.rooms.get(mk[1] ?? "");
    if (!r?.seats.some((x) => x.id === me.id)) return err(404, "not_found", "No such room.");
    const t = rand();
    const email = body.email ? String(body.email) : null;
    r.invites.push({ id: rand().slice(0, 8), token: t, email });
    const url = `${location.origin}/invite/${t}`;
    if (email) db.mailbox.unshift({ to: email, url, at: new Date().toISOString() });
    return json(201, {
      id: t.slice(0, 8),
      url,
      expiresAt: new Date(Date.now() + 7 * 864e5).toISOString(),
    });
  }
  const inv = path.match(/^\/invites\/([a-f0-9]+)(\/accept)?$/);
  if (inv) {
    const r = [...db.rooms.values()].find((x) => x.invites.some((i) => i.token === inv[1]));
    if (!r) return err(404, "invalid_invite", "This invite has expired or was revoked.");
    if (!inv[2]) {
      const creator = db.people.get(r.seats[0]?.id ?? "");
      return json(200, {
        room: {
          id: r.id,
          title: r.title,
          studio: r.studio,
          capabilities: [
            {
              id: r.studio,
              label: r.studio,
              roles: [
                { id: "design", label: "Model and workflow" },
                { id: "materials", label: "Evidence and review" },
              ],
              lanes: (LANES[r.studio] ?? []).map(([id, role]) => ({
                id,
                label: id,
                defaultRole: role,
                fields: [],
              })),
            },
          ],
        },
        inviter: {
          id: creator?.id ?? "",
          name: creator?.name ?? "",
          color: creator?.color ?? "blue",
        },
        seats: roomView(r).seats,
        seated: r.seats.some((x) => x.id === me.id),
        full: r.seats.length >= 4,
        lanes: lanesOf(r),
        expiresAt: new Date(Date.now() + 7 * 864e5).toISOString(),
      });
    }
    if (!r.seats.some((s) => s.id === me.id)) {
      if (r.seats.length >= 4) return err(409, "room_full", "All four seats are taken.");
      r.seats.push({ id: me.id, role: String(body.role) });
    }
    return json(200, { room: r.id, seq: 2 });
  }
  return err(404, "not_found", `The mock has no ${method} ${path}.`);
}
