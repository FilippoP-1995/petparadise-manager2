import { useEffect, useState, type ReactNode } from "react";

import { fetchCurrentUser, login as loginRequest, logout as logoutRequest } from "./api";
import { AuthContext, type CurrentUser } from "./auth-context";

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
