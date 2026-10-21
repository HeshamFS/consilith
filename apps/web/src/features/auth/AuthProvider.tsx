import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import { getSession, setSession, subscribeSession } from "@/shared/http/session";
import { authApi, type Person, type SignedIn } from "./api";

type AuthValue = {
  status: "loading" | "signed-out" | "signed-in";
  person: Person | null;
  signIn: (s: SignedIn) => void;
  signOut: () => Promise<void>;
  setPerson: (p: Person) => void;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const session = useSyncExternalStore(subscribeSession, getSession, getSession);
  const [person, setPerson] = useState<Person | null>(null);
  const [loading, setLoading] = useState<boolean>(session !== null);

  useEffect(() => {
    if (!session) {
      setPerson(null);
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    authApi
      .me()
      .then((r) => live && setPerson(r.person))
      .catch(() => live && setSession(null))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [session]);

  const signIn = useCallback((s: SignedIn) => {
    setPerson(s.person);
    setSession({ token: s.token, sid: s.sid });
  }, []);

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setSession(null);
      setPerson(null);
    }
  }, []);

  const status = loading ? "loading" : session && person ? "signed-in" : "signed-out";
  return (
    <AuthContext value={{ status, person, signIn, signOut, setPerson }}>{children}</AuthContext>
  );
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
