import { agentOf } from "./describe";
import type { RoomDoc } from "./types";

/** How a person reads where an agent's words come from: the API (or plan) and the model, so two
 * agents on different providers are visibly different when they talk to each other. */
const API_NAMES: Record<string, string> = {
  openai: "OpenAI API",
  gemini: "Google Gemini API",
  anthropic: "Anthropic API",
  siwc: "ChatGPT plan",
  scripted: "No model",
};

/** "OpenAI API · gpt-6.1-sol". A brought agent is "A2A agent"; an unknown provider shows its id. */
export function modelLabel(provider: string | undefined, model?: string | null): string {
  if (!provider) return "";
  const api = API_NAMES[provider] ?? provider;
  return model && provider !== "scripted" ? `${api} · ${model}` : api;
}

/** The label for a personal agent by its id (`hesham.agent`) or its owner's id, from the room
 * document. Empty when the room does not know the agent. */
export function agentModelLabel(doc: RoomDoc | null | undefined, agentOrPerson: string): string {
  if (!doc?.room.agents) return "";
  const person = agentOrPerson.replace(/\.agent$/, "");
  const a = agentOf(doc, person);
  if (!a) return "";
  if (a.brought) return "A2A agent";
  return modelLabel(a.config.modelProvider, a.config.modelName);
}
