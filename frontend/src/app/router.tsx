import { createBrowserRouter, ScrollRestoration } from "react-router-dom";

import { LoginPage } from "@/features/auth/LoginPage";
import { ClientFormPage } from "@/features/clients/ClientFormPage";
import { ClientListPage } from "@/features/clients/ClientListPage";
import { PracticeDetailPage } from "@/features/practices/PracticeDetailPage";
import { PracticeFormPage } from "@/features/practices/PracticeFormPage";
import { PracticeListPage } from "@/features/practices/PracticeListPage";
import { VeterinarianFormPage } from "@/features/veterinarians/VeterinarianFormPage";
import { VeterinarianListPage } from "@/features/veterinarians/VeterinarianListPage";

import { AppShell } from "./AppShell";
import { RequireAuth } from "./RequireAuth";

function Root() {
  return (
    <>
      <AppShell />
      {/* Ripristina lo scroll tornando indietro nella cronologia - stesso
          problema risolto pagina per pagina in V1 (vedi Fatture in questa
          sessione), qui e' gratuito per ogni pagina grazie al pattern
          condiviso (vedi shared/useListQueryParams.ts). */}
      <ScrollRestoration />
    </>
  );
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: (
      <RequireAuth>
        <Root />
      </RequireAuth>
    ),
    children: [
      { path: "/clienti", element: <ClientListPage /> },
      { path: "/clienti/nuovo", element: <ClientFormPage /> },
      { path: "/veterinari", element: <VeterinarianListPage /> },
      { path: "/veterinari/nuovo", element: <VeterinarianFormPage /> },
      { path: "/pratiche", element: <PracticeListPage /> },
      { path: "/pratiche/nuova", element: <PracticeFormPage /> },
      { path: "/pratiche/:practiceId", element: <PracticeDetailPage /> },
      { path: "/", element: <PracticeListPage /> },
    ],
  },
]);
