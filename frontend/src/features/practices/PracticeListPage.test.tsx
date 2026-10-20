import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { formatMoney } from "@/shared/money";

import { PracticeListPage } from "./PracticeListPage";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: vi.fn(),
    PUT: vi.fn(),
    DELETE: vi.fn(),
  },
}));

const PRACTICES = [
  {
    id: 1,
    practice_number: "CR-000001",
    status: "ritirato",
    request_origin: "Privato",
    service_type: "Cremazione singola",
    destination_branch_id: 7,
    veterinarian_id: 3,
    notes: "Nota di prova",
    tags: [42],
    owner_snapshot: { first_name: "Mario", last_name: "Rossi", phone: "333123456" },
    animals: [{ id: 1, name: "Fido", species: "Cane", age_years: 5, age_months: null, estimated_weight_grams: 12000, sort_order: 0 }],
    created_at: "2026-08-10T09:00:00Z",
    effective_total_cents: 34000,
  },
  {
    id: 2,
    practice_number: "CR-000002",
    status: "in_programma",
    request_origin: "Collaboratore",
    service_type: "Cremazione singola",
    destination_branch_id: 7,
    veterinarian_id: null,
    notes: null,
    tags: [],
    owner_snapshot: null,
    animals: [],
    created_at: "2026-07-02T09:00:00Z",
    effective_total_cents: 0,
  },
];

// La pratica 2 e' deliberatamente ASSENTE dal riepilogo bulk (simula il
// caso reale in cui una pratica non ha ancora movimenti/non e' presente
// nella risposta), cosi' i test sul fallback "-" restano deterministici -
// un riepilogo presente con importi a zero mostrerebbe invece "0,00 €"
// (V1: paid_cell mostra sempre "€ 0,00 {canale}", mai un trattino).
const PAYMENTS_SUMMARY = [
  {
    practice_id: 1,
    paid_total_cents: 34000,
    acconto_total_cents: 10000,
    invoices: [{ invoice_number: "FT-2026-001", total_amount_cents: 34000, channel: "W" }],
  },
];

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/practices") return Promise.resolve({ data: PRACTICES, error: undefined });
    if (path === "/api/veterinarians") return Promise.resolve({ data: [{ id: 3, clinic_name: "Clinica Veterinaria Test", doctor_name: null, short_name: null }], error: undefined });
    if (path === "/api/references/company-locations") return Promise.resolve({ data: [{ id: 7, name: "Livorno" }], error: undefined });
    if (path === "/api/references/tags") return Promise.resolve({ data: [{ id: 42, code: "URG", label: "Urgente", category: null }], error: undefined });
    if (path === "/api/payments/practice-summary") return Promise.resolve({ data: PAYMENTS_SUMMARY, error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/pratiche"]}>
        <Routes>
          <Route path="/pratiche" element={<PracticeListPage />} />
          <Route path="/pratiche/:practiceId" element={<div>Dettaglio pratica</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PracticeListPage - colonne (V1: 14 colonne, qui quelle risolvibili senza toccare l'area economica)", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("mostra veterinario e sede risolti da id a nome tramite gli endpoint di riferimento gia' esistenti", async () => {
    renderPage();
    expect(await screen.findByText("Clinica Veterinaria Test")).toBeInTheDocument();
    expect(screen.getAllByText("Livorno")).toHaveLength(2);
  });

  it("mostra proprietario, animale, note ed etichetta della pratica", async () => {
    renderPage();
    await screen.findByText("CR-000001");
    expect(screen.getByText(/Mario Rossi/)).toBeInTheDocument();
    expect(screen.getByText("Fido", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("Nota di prova")).toBeInTheDocument();
    expect(screen.getByText("Urgente")).toBeInTheDocument();
  });

  it("una pratica senza veterinario/note/tag mostra '-' invece di un vuoto o un crash", async () => {
    renderPage();
    await screen.findByText("CR-000002");
    // La riga di CR-000002 non deve avere ne' "Clinica Veterinaria Test" ne' etichette.
    const row = screen.getByText("CR-000002").closest("tr");
    expect(row).not.toBeNull();
    expect(row!.textContent).toContain("-");
  });

  it("mostra Totale pagato e Fattura dal riepilogo bulk (nessuna query N+1)", async () => {
    renderPage();
    await screen.findByText("CR-000001");
    expect(await screen.findByText("FT-2026-001")).toBeInTheDocument();
    // Una sola chiamata al riepilogo bulk, non una per pratica.
    const summaryCalls = getMock.mock.calls.filter((call) => call[0] === "/api/payments/practice-summary");
    expect(summaryCalls).toHaveLength(1);
  });

  it("una pratica senza pagamenti mostra '-' nelle colonne Totale pagato/Fattura/Acconto/Rimanenza", async () => {
    renderPage();
    // Attende che il riepilogo bulk pagamenti sia arrivato (FT-2026-001 e'
    // della pratica 1) prima di leggere le celle della pratica 2, cosi'
    // l'assert non dipende dal timing della seconda query asincrona.
    await screen.findByText("FT-2026-001");
    const row = screen.getByText("CR-000002").closest("tr");
    const headerCells = [...screen.getAllByRole("columnheader")].map((th) => th.textContent);
    const cells = row!.querySelectorAll("td");
    const byHeader = (label: string) => cells[headerCells.indexOf(label)].textContent;
    expect(byHeader("Totale pagato")).toBe("-");
    expect(byHeader("Fattura")).toBe("-");
    expect(byHeader("Acconto")).toBe("-");
    expect(byHeader("Rimanenza")).toBe("-");
  });

  it("V1 (Totale W/Totale D/Acconto/Rimanenza): la pratica con pagamenti mostra i 4 valori reali, 'Totale D' resta sempre '-' (nessun equivalente V2)", async () => {
    renderPage();
    await screen.findByText("FT-2026-001");
    const row = screen.getByText("CR-000001").closest("tr");
    const headerCells = [...screen.getAllByRole("columnheader")].map((th) => th.textContent);
    const cells = row!.querySelectorAll("td");
    const byHeader = (label: string) => cells[headerCells.indexOf(label)].textContent;
    expect(byHeader("Totale W")).toBe(formatMoney(34000));
    expect(byHeader("Totale D")).toBe("-");
    expect(byHeader("Acconto")).toBe(formatMoney(10000));
    expect(byHeader("Rimanenza")).toBe(formatMoney(0));
  });
});

describe("PracticeListPage - raggruppamento mensile (V1: sezioni collassabili per mese)", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("raggruppa le pratiche in sezioni separate per mese di creazione", async () => {
    renderPage();
    expect(await screen.findByText("Agosto 2026")).toBeInTheDocument();
    expect(screen.getByText("Luglio 2026")).toBeInTheDocument();
  });

  it("collassare una sezione ne nasconde la tabella senza toccare le altre", async () => {
    renderPage();
    const user = userEvent.setup();
    await screen.findByText("Agosto 2026");

    const augustHeader = screen.getByText("Agosto 2026").closest(".month-title") as HTMLElement;
    await user.click(augustHeader);

    await waitFor(() => expect(screen.queryByText("CR-000001")).not.toBeInTheDocument());
    // Luglio resta visibile e invariato.
    expect(screen.getByText("CR-000002")).toBeInTheDocument();
  });
});

describe("PracticeListPage - click verso il dettaglio", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("il codice pratica e' un link verso /pratiche/:id", async () => {
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("link", { name: "CR-000001" }));
    expect(await screen.findByText("Dettaglio pratica")).toBeInTheDocument();
  });
});
