import { useContext } from "react";

import { AuthContext, type AuthState } from "./auth-context";

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth deve essere usato dentro AuthProvider");
  return ctx;
}
