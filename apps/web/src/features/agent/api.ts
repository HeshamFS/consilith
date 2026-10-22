import type { Autonomy } from "@/shared/domain/disciplines";
import { api } from "@/shared/http/client";

/** A provider id. The list of providers is data from `GET /providers`, not a type here. */
export type Provider = string;

export type ProviderAuth = "key" | "chatgpt" | "brought" | "none";

/** One entry of the server's provider registry (`GET /providers`). */
export type ProviderInfo = {
  id: Provider;
  label: string;
  auth: ProviderAuth;
  needsModel: boolean;
  defaultModel?: string;
  dataTerms: string;
};

/** Where a connection stands. The server sends every state but `validating` and `invalid`; the
 * panel adds those two itself while it saves a key. (A ChatGPT sign-in in progress is also
 * `validating`.) */
export type CredentialState =
  | "not_connected"
  | "validating"
  | "ready"
  | "invalid"
  | "expired"
  | "needs_reentry";

export type ProviderCredential = {
  state: CredentialState;
  /** One line on how long this connection lasts. */
  lifetime: string;
  /** The server's own words (ChatGPT): why it is not ready. */
  reason?: string | null;
};

export type Credential = ProviderCredential & {
  provider: Provider;
  byProvider: Record<Provider, ProviderCredential>;
};

export type MyAgent = {
  kind: "consilith" | "brought";
  provider: Provider;
  model: string | null;
  autonomy: Autonomy;
  cardUrl: string | null;
  hasKey: boolean;
  /** Model calls per hour this person set, or null for the default. */
  hourlyCap?: number | null;
  credential?: Credential;
};

export type ModelOption = { id: string; label: string; verified?: boolean };

/** The registry, filled in place by `loadProviders()`. Empty until the first load finishes, so
 * code that only wants a label falls back to the id. */
export const PROVIDERS: ProviderInfo[] = [];

let loading: Promise<ProviderInfo[]> | null = null;

/** Load the provider registry once. A failed load can be tried again. */
export function loadProviders(): Promise<ProviderInfo[]> {
  if (!loading) {
    loading = api.get<{ providers: ProviderInfo[] }>("/providers").then(
      (r) => {
        PROVIDERS.splice(0, PROVIDERS.length, ...r.providers);
        return PROVIDERS;
      },
      (e: unknown) => {
        loading = null;
        throw e;
      },
    );
  }
  return loading;
}

/** Forget the cached registry (tests, and a retry after a failure). */
export function resetProviders(): void {
  loading = null;
  PROVIDERS.splice(0, PROVIDERS.length);
}

export function providerLabel(id: Provider, list: ProviderInfo[] = PROVIDERS): string {
  return list.find((p) => p.id === id)?.label ?? id;
}

export const agentApi = {
  get: () => api.get<MyAgent>("/me/agent"),
  save: (
    a: Pick<MyAgent, "kind" | "provider" | "model" | "autonomy" | "cardUrl"> & {
      hourlyCap?: number | null;
    },
  ) => api.put<MyAgent>("/me/agent", a),
  providers: () => loadProviders(),
  /** The key is sent once. The server checks it with the provider and keeps it in memory for
   * this session only; a key the provider refuses is an error here and is not kept. */
  setKey: (provider: Provider, key: string) => api.put<void>("/me/agent/key", { provider, key }),
  dropKey: () => api.del<void>("/me/agent/key"),
  siwcStatus: () =>
    api.get<{
      available: boolean;
      signedIn: boolean;
      signingIn?: boolean;
      email?: string;
      reason?: string;
    }>("/me/agent/siwc"),
  siwcLogin: () => api.post<{ url: string }>("/me/agent/siwc/login"),
  siwcLogout: () => api.post<{ signedIn: false }>("/me/agent/siwc/logout"),
  /** The provider's own list for the key this session holds. Models in the operator's table come
   * first; the rest are unverified. */
  models: (provider: Provider) =>
    api.get<{ provider: Provider; models: ModelOption[] }>(
      `/me/agent/models?provider=${encodeURIComponent(provider)}`,
    ),
};

/** The same first checks the room server runs; it also resolves the host and refuses private
 * addresses, redirects and oversized cards. */
export function cardUrlProblem(url: string): string | null {
  const u = url.trim();
  if (!u) return "Paste the agent's card URL.";
  if (!u.startsWith("https://")) return "It must start with https://.";
  if (!u.endsWith("/.well-known/agent-card.json"))
    return "It must end with /.well-known/agent-card.json.";
  try {
    const parsed = new URL(u);
    if (parsed.username || parsed.password)
      return "A card URL never carries a user name or password.";
  } catch {
    return "That is not a valid URL.";
  }
  return null;
}
