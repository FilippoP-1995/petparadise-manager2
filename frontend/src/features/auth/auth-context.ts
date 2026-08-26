import { createContext } from "react";

import type { components } from "@/shared/api/schema";

// Tipo derivato dallo schema OpenAPI generato, mai duplicato a mano
// (doc10 "non duplicare manualmente i tipi delle API").
export type CurrentUser = components["schemas"]["LoginResponse"];

export interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

// Contesto isolato in un modulo separato (nessun export di componenti qui):
// AuthContext.tsx esporta solo il componente AuthProvider e useAuth.ts esporta
// solo l'hook, cosi' ogni file resta un "Fast Refresh boundary" valido per
// Vite (un file che mischia componenti ed export non-componente perde il
// Fast Refresh e viene invalidato per intero, causando in dev un errore
// intermittente "useAuth deve essere usato dentro AuthProvider" quando il
// remount non e' atomico - vedi doc09/review hardening Fase 4).
export const AuthContext = createContext<AuthState | null>(null);
