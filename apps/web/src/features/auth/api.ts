import { api } from "@/shared/http/client";

export type Discipline = string;

export type Person = {
  id: string;
  email: string;
  name: string;
  color: string;
  discipline: Discipline;
  profileComplete: boolean;
};

export type SignedIn = { token: string; sid: string; person: Person; needsProfile: boolean };
export type Providers = { email: boolean; github: boolean; devMailbox: boolean };
export type MailItem = { to: string; url: string; at: string };

export const authApi = {
  providers: () => api.get<Providers>("/auth/providers"),
  emailLink: (email: string) => api.post<{ sent: boolean }>("/auth/email", { email }),
  redeemLink: (token: string) => api.post<SignedIn>("/auth/email/redeem", { token }),
  redeemTicket: (ticket: string) => api.post<SignedIn>("/auth/ticket/redeem", { ticket }),
  logout: () => api.post<void>("/auth/logout"),
  me: () => api.get<{ person: Person; sid: string }>("/me"),
  saveProfile: (p: { name: string; color: string; discipline: Discipline }) =>
    api.put<{ person: Person }>("/me", p),
  mailbox: () => api.get<MailItem[]>("/dev/mailbox"),
};

export function githubStartUrl(returnTo: string): string {
  return `/api/auth/github/start?return=${encodeURIComponent(returnTo)}`;
}
