import type { Autonomy } from "@/shared/domain/disciplines";
import { api } from "@/shared/http/client";

export type Provider = "anthropic" | "openai" | "gemini" | "scripted" | "siwc";

export type MyAgent = {
  kind: "consilith" | "brought";
  provider: Provider;
  model: string | null;
  autonomy: Autonomy;
  cardUrl: string | null;
  hasKey: boolean;
};

export const PROVIDERS: { id: Provider; label: string }[] = [
  { id: "anthropic", label: "Anthropic" },
  { id: "openai", label: "OpenAI" },
  { id: "gemini", label: "Google Gemini" },
  { id: "scripted", label: "No model" },
  { id: "siwc", label: "Your ChatGPT plan" },
];

export const agentApi = {
  get: () => api.get<MyAgent>("/me/agent"),
  save: (a: Omit<MyAgent, "hasKey">) => api.put<MyAgent>("/me/agent", a),
  /** The key is sent once; the server keeps it in memory for this session only. */
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
  /** The provider's own list for the key this session holds, newest first. */
  models: (provider: Provider) =>
    api.get<{ provider: Provider; models: { id: string; label: string }[] }>(
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
