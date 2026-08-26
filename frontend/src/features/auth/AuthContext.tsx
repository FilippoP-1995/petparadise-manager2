import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { components } from "@/shared/api/schema";

import { fetchCurrentUser, login as loginRequest, logout as logoutRequest } from "./api";

// Tipo derivato dallo schema OpenAPI generato, mai duplicato a mano
// (doc10 "non duplicare manualmente i tipi delle API").
type CurrentUser = components["schemas"]["LoginResponse"];

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchCurrentUser()
      .then(setUser)
      .finally(() => setLoading(false));
  }, []);

  async function login(username: string, password: string) {
    await loginRequest(username, password);
    setUser(await fetchCurrentUser());
  }

  async function logout() {
    await logoutRequest();
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth deve essere usato dentro AuthProvider");
  return ctx;
}
