import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PracticeSignaturePage } from "./PracticeSignaturePage";

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
  notes: null,
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
    return Promise.resolve({ data: undefined, error: { detail: `unmocked GET ${path}` } });
  });
}

// Simula lo stesso gesto ascoltato dal componente (mousedown/mousemove/
// mouseup sul canvas, identico a V1: canvas.addEventListener('mousedown',
// start) ecc., app.py:17428) - piu' affidabile in jsdom del livello di
// astrazione pointer di user-event, che dipende dal layout reale assente qui.
function draw(canvas: HTMLCanvasElement) {
  fireEvent.mouseDown(canvas, { clientX: 10, clientY: 10 });
  fireEvent.mouseMove(canvas, { clientX: 50, clientY: 40 });
  fireEvent.mouseUp(canvas, { clientX: 50, clientY: 40 });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/pratiche/5/firma"]}>
        <Routes>
          <Route path="/pratiche/:practiceId" element={<div>Dettaglio pratica</div>} />
          <Route path="/pratiche/:practiceId/firma" element={<PracticeSignaturePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PracticeSignaturePage - firma proprietario (V1: signature_page, app.py:17421)", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
    setupApiMock();
    // jsdom non implementa un vero canvas 2D - la stessa API di disegno di
    // V1 (mousedown/mousemove/mouseup su un context 2d) viene simulata qui
    // per verificare che il componente la esponga correttamente, non che
    // jsdom sappia davvero disegnare.
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      clearRect: vi.fn(),
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.toDataURL = vi.fn(() => "data:image/png;base64,SIGNATURE");
  });

  it("il submit senza aver firmato mostra un errore e non chiama l'API", async () => {
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Salva firma" }));

    expect(await screen.findByText(/Firma non valida/)).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });

  it("firmare e salvare invia signature_data lasciando line_items invariati", async () => {
    putMock.mockResolvedValue({ data: { ...PRACTICE, signature_data: "data:image/png;base64,SIGNATURE" }, error: undefined });
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "Salva firma" });

    // <canvas> non ha un ruolo ARIA implicito standard: lo selezioniamo
    // direttamente dal DOM, come farebbe l'utente col dito sullo schermo.
    const padEl = document.querySelector("canvas.signature-pad") as HTMLCanvasElement;
    expect(padEl).toBeTruthy();
    draw(padEl);

    await user.click(screen.getByRole("button", { name: "Salva firma" }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const [, options] = putMock.mock.calls[0];
    expect(options.body.signature_data).toBe("data:image/png;base64,SIGNATURE");
    expect(options.body.line_items).toEqual([
      { category: "cremazione", description: "Cremazione singola", subtype: null, amount_cents: 15000, channel: "W", urn_catalog_id: null },
    ]);
    expect(await screen.findByText("Dettaglio pratica")).toBeInTheDocument();
  });

  it("'Cancella' azzera il disegno cosi' che il submit torni a rifiutare senza firma", async () => {
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "Salva firma" });
    const padEl = document.querySelector("canvas.signature-pad") as HTMLCanvasElement;

    draw(padEl);
    await user.click(screen.getByRole("button", { name: "Cancella" }));
    await user.click(screen.getByRole("button", { name: "Salva firma" }));

    expect(await screen.findByText(/Firma non valida/)).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });
});
