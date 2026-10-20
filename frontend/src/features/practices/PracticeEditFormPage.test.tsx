import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PracticeEditFormPage } from "./PracticeEditFormPage";

const { getMock, putMock } = vi.hoisted(() => ({ getMock: vi.fn(), putMock: vi.fn() }));

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: vi.fn(),
    PUT: (...args: unknown[]) => putMock(...args),
    DELETE: vi.fn(),
  },
}));

const PRACTICE = {
  id: 5,
  practice_number: "CR-000005",
  status: "ritirato",
  request_origin: "Collaboratore",
  originating_pickup_event_id: null,
  destination_branch_id: 7,
  client_id: 9,
  service_type: "Cremazione singola",
  collaborator_id: null,
  veterinarian_id: null,
  origin_veterinarian_id: null,
  pickup_type: "domicilio",
  pickup_location_id: null,
  pickup_zone_id: null,
  pickup_address: null,
  pickup_contact_name: null,
  provenance_code: null,
  microchip: null,
  notes: "Nota originale",
  ddt_number: null,
  ddt_date: null,
  ddt_pdf_path: null,
  signature_data: null,
  data_complete: false,
  owner_snapshot: { first_name: "Mario", last_name: "Rossi" },
  transport_method: null,
  vehicle_plate: null,
  temperature_mode: null,
  package_count: null,
  container_id: null,
  lot_number: null,
  treatment_method: null,
  delivery_at_clinic: false,
  delivery_at_home: false,
  signatory_identity_document_number: null,
  signatory_identity_document_date: null,
  signatory_signing_place: null,
  ddt_share_token: null,
  original_practice_number: null,
  computed_total_override_cents: null,
  computed_total_override_reason: null,
  computed_total_override_at: null,
  to_invoice: false,
  send_catalog: false,
  catalog_sent: false,
  send_estremi: false,
  estremi_sent: false,
  voucher_requested: false,
  use_voucher: false,
  whatsapp_thanks_sent_at: null,
  no_whatsapp_message: false,
  cremation_registered: false,
  cremation_queued: false,
  collaborator_billing_status: "non_dovuto",
  collaborator_billing_invoiced_at: null,
  collaborator_name_fallback: null,
  owner_notified_status: "non_inviato",
  owner_notified_at: null,
  used_voucher_id: null,
  deleted_at: null,
  created_at: "2026-08-10T09:00:00Z",
  updated_at: "2026-08-10T09:00:00Z",
  animals: [{ id: 1, sort_order: 0, name: "Fido", species: "Cane", breed: null, age_years: 5, age_months: null, estimated_weight_grams: 12000, microchip: null, cremation_type: null }],
  line_items: [
    { id: 1, sort_order: 0, category: "cremazione", description: "Cremazione singola", subtype: null, amount_cents: 15000, channel: "W", urn_catalog_id: null },
  ],
  tags: [],
  line_items_total_cents: 15000,
  effective_total_cents: 15000,
};

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/practices/{practice_id}") return Promise.resolve({ data: PRACTICE, error: undefined });
    if (path === "/api/references/company-locations") return Promise.resolve({ data: [{ id: 7, name: "Sede Empoli" }], error: undefined });
    if (path === "/api/veterinarians") return Promise.resolve({ data: [{ id: 2, short_name: "DEL PERO", clinic_name: "Ambulatorio", hours: [] }], error: undefined });
    if (path === "/api/references/collaborators") return Promise.resolve({ data: [{ id: 3, name: "Collab SRL" }], error: undefined });
    if (path === "/api/references/tags") return Promise.resolve({ data: [{ id: 1, code: "assistita", label: "Assistita", category: null }], error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked GET ${path}` } });
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/pratiche/5/modifica"]}>
        <Routes>
          <Route path="/pratiche/:practiceId" element={<div>Dettaglio pratica</div>} />
          <Route path="/pratiche/:practiceId/modifica" element={<PracticeEditFormPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PracticeEditFormPage - modifica pratica (V1: edit_page, prima volta reale in V2)", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
    setupApiMock();
  });

  it("precompila i campi con i dati reali della pratica caricata", async () => {
    renderPage();
    expect(await screen.findByDisplayValue("Nota originale")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Fido")).toBeInTheDocument();
    // Il cliente non e' un campo modificabile (doc06 Addendum A): mostrato
    // sola lettura, non un input.
    expect(screen.getByText(/Mario Rossi/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Mario Rossi")).not.toBeInTheDocument();
  });

  it("modificare un campo semplice e salvare invia line_items invariati (nessun effetto economico)", async () => {
    putMock.mockResolvedValue({ data: { ...PRACTICE, notes: "Nota corretta" }, error: undefined });
    renderPage();
    const user = userEvent.setup();

    const notesField = await screen.findByDisplayValue("Nota originale");
    await user.clear(notesField);
    await user.type(notesField, "Nota corretta");
    await user.click(screen.getAllByRole("button", { name: "Salva modifiche" })[0]);

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [, options] = putMock.mock.calls[0];
    expect(options.body.notes).toBe("Nota corretta");
    expect(options.body.line_items).toEqual([
      { category: "cremazione", description: "Cremazione singola", subtype: null, amount_cents: 15000, channel: "W", urn_catalog_id: null },
    ]);
  });

  it("modificare il veterinario invia il nuovo id senza toccare gli altri campi", async () => {
    putMock.mockResolvedValue({ data: PRACTICE, error: undefined });
    renderPage();
    const user = userEvent.setup();

    await screen.findByDisplayValue("Nota originale");
    await user.selectOptions(screen.getByRole("combobox", { name: "Veterinario (punto di affido)" }), "2");
    await user.click(screen.getAllByRole("button", { name: "Salva modifiche" })[0]);

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [, options] = putMock.mock.calls[0];
    expect(options.body.veterinarian_id).toBe(2);
    expect(options.body.destination_branch_id).toBe(7);
  });

  it("modificare l'animale aggiorna solo quell'animale nel payload", async () => {
    putMock.mockResolvedValue({ data: PRACTICE, error: undefined });
    renderPage();
    const user = userEvent.setup();

    const nameField = await screen.findByDisplayValue("Fido");
    await user.clear(nameField);
    await user.type(nameField, "Rex");
    await user.click(screen.getAllByRole("button", { name: "Salva modifiche" })[0]);

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [, options] = putMock.mock.calls[0];
    expect(options.body.animals).toEqual([
      { name: "Rex", species: "Cane", breed: null, age_years: 5, age_months: null, estimated_weight_grams: 12000, microchip: null, cremation_type: null },
    ]);
  });

  it("un errore del backend mostra il messaggio e NON naviga via dalla pagina", async () => {
    putMock.mockResolvedValue({ data: undefined, error: { detail: "Sede di destinazione non trovata" } });
    renderPage();
    const user = userEvent.setup();

    await screen.findByDisplayValue("Nota originale");
    await user.click(screen.getAllByRole("button", { name: "Salva modifiche" })[0]);

    expect(await screen.findByText("Sede di destinazione non trovata")).toBeInTheDocument();
    expect(screen.queryByText("Dettaglio pratica")).not.toBeInTheDocument();
  });

  it("un salvataggio riuscito torna al dettaglio pratica", async () => {
    putMock.mockResolvedValue({ data: PRACTICE, error: undefined });
    renderPage();
    const user = userEvent.setup();

    await screen.findByDisplayValue("Nota originale");
    await user.click(screen.getAllByRole("button", { name: "Salva modifiche" })[0]);

    expect(await screen.findByText("Dettaglio pratica")).toBeInTheDocument();
  });
});
