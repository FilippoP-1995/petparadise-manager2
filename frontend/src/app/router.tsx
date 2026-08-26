import { createBrowserRouter, ScrollRestoration } from "react-router-dom";

import { LoginPage } from "@/features/auth/LoginPage";
import { ArticleListPage } from "@/features/articles/ArticleListPage";
import { CalendarPage } from "@/features/calendar/CalendarPage";
import { ClientFormPage } from "@/features/clients/ClientFormPage";
import { ClientListPage } from "@/features/clients/ClientListPage";
import { CompanyLocationFormPage } from "@/features/company-locations/CompanyLocationFormPage";
import { CompanyLocationListPage } from "@/features/company-locations/CompanyLocationListPage";
import { CremationCycleDetailPage } from "@/features/cremation-cycles/CremationCycleDetailPage";
import { CremationCycleFormPage } from "@/features/cremation-cycles/CremationCycleFormPage";
import { CremationCycleListPage } from "@/features/cremation-cycles/CremationCycleListPage";
import { DeliveryDetailPage } from "@/features/deliveries/DeliveryDetailPage";
import { DeliveryFormPage } from "@/features/deliveries/DeliveryFormPage";
import { DeliveryListPage } from "@/features/deliveries/DeliveryListPage";
import { InvoiceDetailPage } from "@/features/invoices/InvoiceDetailPage";
import { InvoiceFormPage } from "@/features/invoices/InvoiceFormPage";
import { InvoiceListPage } from "@/features/invoices/InvoiceListPage";
import { PracticeDetailPage } from "@/features/practices/PracticeDetailPage";
import { PracticeFormPage } from "@/features/practices/PracticeFormPage";
import { PracticeListPage } from "@/features/practices/PracticeListPage";
import { PickupDetailPage } from "@/features/pickups/PickupDetailPage";
import { PickupFormPage } from "@/features/pickups/PickupFormPage";
import { PickupListPage } from "@/features/pickups/PickupListPage";
import { UrnDetailPage } from "@/features/urns/UrnDetailPage";
import { UrnFormPage } from "@/features/urns/UrnFormPage";
import { UrnListPage } from "@/features/urns/UrnListPage";
import { VeterinarianFormPage } from "@/features/veterinarians/VeterinarianFormPage";
import { VeterinarianListPage } from "@/features/veterinarians/VeterinarianListPage";

import { AppShell } from "./AppShell";
import { RequireAdmin, RequireAuth } from "./RequireAuth";

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
      { path: "/calendario", element: <CalendarPage /> },
      { path: "/clienti", element: <ClientListPage /> },
      { path: "/clienti/nuovo", element: <ClientFormPage /> },
      { path: "/veterinari", element: <VeterinarianListPage /> },
      { path: "/veterinari/nuovo", element: <VeterinarianFormPage /> },
      { path: "/pratiche", element: <PracticeListPage /> },
      { path: "/pratiche/nuova", element: <PracticeFormPage /> },
      { path: "/pratiche/:practiceId", element: <PracticeDetailPage /> },
      { path: "/ritiri", element: <PickupListPage /> },
      { path: "/ritiri/nuovo", element: <PickupFormPage /> },
      { path: "/ritiri/:pickupId", element: <PickupDetailPage /> },
      { path: "/riconsegne", element: <DeliveryListPage /> },
      { path: "/riconsegne/nuova", element: <DeliveryFormPage /> },
      { path: "/riconsegne/:deliveryId", element: <DeliveryDetailPage /> },
      { path: "/cicli-cremazione", element: <CremationCycleListPage /> },
      { path: "/cicli-cremazione/nuovo", element: <CremationCycleFormPage /> },
      { path: "/cicli-cremazione/:cycleId", element: <CremationCycleDetailPage /> },
      { path: "/sedi", element: <CompanyLocationListPage /> },
      {
        path: "/sedi/nuova",
        element: (
          <RequireAdmin>
            <CompanyLocationFormPage />
          </RequireAdmin>
        ),
      },
      {
        path: "/sedi/:locationId/modifica",
        element: (
          <RequireAdmin>
            <CompanyLocationFormPage />
          </RequireAdmin>
        ),
      },
      { path: "/catalogo-urne", element: <UrnListPage /> },
      { path: "/catalogo-urne/nuova", element: <UrnFormPage /> },
      { path: "/catalogo-urne/:urnId", element: <UrnDetailPage /> },
      { path: "/catalogo-urne/:urnId/modifica", element: <UrnFormPage /> },
      { path: "/prodotti", element: <ArticleListPage /> },
      { path: "/fatture", element: <InvoiceListPage /> },
      { path: "/fatture/nuova", element: <InvoiceFormPage /> },
      { path: "/fatture/:invoiceId", element: <InvoiceDetailPage /> },
      { path: "/", element: <PracticeListPage /> },
    ],
  },
]);
