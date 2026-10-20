import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Practice } from "@/features/practices/api";

import { PracticePaymentsSection } from "./PracticePaymentsSection";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: (...args: unknown[]) => postMock(...args),
    PUT: vi.fn(),
    DELETE: vi.fn(),
  },
}));

vi.mock("@/features/auth/useAuth", () => ({
  useAuth: () => ({
    user: { user_id: 1, display_name: "Admin", role: "admin" },
    loading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

const PRACTICE = {
  id: 1,
  computed_total_override_cents: null,
  computed_total_override_reason: null,
  line_items_total_cents: 10000,
  collaborator_id: null,
  collaborator_billing_status: "da_fatturare",
} as unknown as Practice;

const RECON = {
  effective_total_cents: 10000,
  paid_w_cents: 3000,
  paid_d_cents: 0,
  paid_collaboratori_cents: 0,
  paid_total_cents: 3000,
  residual_cents: 7000,
  status: "parziale",
};

const PAYMENTS = [{ id: 1, movement_date: "2026-08-20", movement_type: "Acconto", channel: "W", amount_cents: 3000 }];

const INVOICES = [
  { id: 10, invoice_number: "F-2026-010", total_amount_cents: 5000, channel: "W" },
  { id: 11, invoice_number: "F-2026-011", total_amount_cents: 5000, channel: "D" },
];

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/payments/practice/{practice_id}/riconciliazione") return Promise.resolve({ data: RECON, error: undefined });
    if (path === "/api/payments") return Promise.resolve({ data: PAYMENTS, error: undefined });
    if (path === "/api/invoices") return Promise.resolve({ data: INVOICES, error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
  });
}

function FatturaStub() {
  return (
    <div>
      <p>Fattura stub</p>
      <Link to={-1 as unknown as string}>Indietro</Link>
    </div>
  );
}

function PraticaWrapper() {
  return <PracticePaymentsSection practice={PRACTICE} />;
}

function renderWithRouter() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/pratiche/1"]}>
        <Routes>
          <Route path="/pratiche/:practiceId" element={<PraticaWrapper />} />
          <Route path="/fatture/:invoiceId" element={<FatturaStub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PracticePaymentsSection - continuita' di navigazione (fattura selezionata per il collegamento)", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("nessuna fattura selezionata all'apertura della pagina", async () => {
    renderWithRouter();
    await screen.findByText("Collega a fattura...");
    const select = screen.getByText("Collega a fattura...").closest("select");
    expect(select).toHaveValue("");
  });

  it("la fattura selezionata per il collegamento sopravvive a una navigazione verso un'altra fattura seguita da Indietro", async () => {
    renderWithRouter();
    const user = userEvent.setup();

    await screen.findByText("Collega a fattura...");
    const select = screen.getByText("Collega a fattura...").closest("select") as HTMLSelectElement;
    await user.selectOptions(select, "10");
    await waitFor(() => expect(select).toHaveValue("10"));

    await user.click(screen.getByRole("link", { name: /F-2026-011.*\(D\)/ }));
    await waitFor(() => expect(screen.getByText("Fattura stub")).toBeInTheDocument());

    await user.click(screen.getByRole("link", { name: "Indietro" }));

    await waitFor(() => {
      const restoredSelect = screen.getByText("Collega a fattura...").closest("select");
      expect(restoredSelect).toHaveValue("10");
    });
  });
});

describe("PracticePaymentsSection - modal di registrazione pagamento (V1: popup, non form sempre visibile)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    setupApiMock();
  });

  it("il form di registrazione non e' visibile finche' non si apre il modal", async () => {
    renderWithRouter();
    await screen.findByRole("button", { name: "Registra pagamento" });
    expect(screen.queryByPlaceholderText("Importo €")).not.toBeInTheDocument();
  });

  it("apre il modal, invia il pagamento con idempotency_key e lo richiude (onDone)", async () => {
    postMock.mockImplementation((path: string, opts: { body?: Record<string, unknown> }) => {
      if (path === "/api/payments") {
        expect(opts.body?.idempotency_key).toEqual(expect.any(String));
        return Promise.resolve({ data: { id: 99, practice_id: 1 }, error: undefined });
      }
      return Promise.resolve({ data: undefined, error: { detail: `unmocked POST ${path}` } });
    });
    renderWithRouter();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Registra pagamento" }));
    const amountInput = await screen.findByPlaceholderText("Importo €");
    await user.type(amountInput, "50,00");
    // Il bottone "Registra pagamento" dentro il modal e quello che lo apre hanno lo stesso nome:
    // dopo l'apertura ce ne sono due, l'ultimo e' quello di submit nel form.
    const submitButtons = screen.getAllByRole("button", { name: "Registra pagamento" });
    await user.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/api/payments", expect.anything()));
    await waitFor(() => expect(screen.queryByPlaceholderText("Importo €")).not.toBeInTheDocument());
  });

  it("'Annulla' chiude il modal senza inviare nulla", async () => {
    renderWithRouter();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Registra pagamento" }));
    await screen.findByPlaceholderText("Importo €");
    await user.click(screen.getByRole("button", { name: "Annulla" }));

    expect(screen.queryByPlaceholderText("Importo €")).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalled();
  });
});

describe("PracticePaymentsSection - modal di storno (V1: popup con motivo obbligatorio, non window.prompt)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    setupApiMock();
  });

  it("il pulsante 'Storna' apre un modal, non window.prompt", async () => {
    const promptSpy = vi.spyOn(window, "prompt");
    renderWithRouter();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Storna" }));
    expect(promptSpy).not.toHaveBeenCalled();
    expect(await screen.findByRole("heading", { name: "Storna pagamento" })).toBeInTheDocument();
    promptSpy.mockRestore();
  });

  it("il submit senza motivo mostra un errore di validazione e non chiama l'API", async () => {
    renderWithRouter();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Storna" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Storna" }));

    expect(await screen.findByText("Il motivo è obbligatorio.")).toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalledWith("/api/payments/{payment_id}/storna", expect.anything());
  });

  it("il submit con motivo chiama reverse con il payment_id e il motivo corretti", async () => {
    postMock.mockImplementation((path: string, opts: { params?: { path?: { payment_id?: number } }; body?: { reason?: string } }) => {
      if (path === "/api/payments/{payment_id}/storna") {
        expect(opts.params?.path?.payment_id).toBe(1);
        expect(opts.body?.reason).toBe("Importo sbagliato");
        return Promise.resolve({ data: { id: 2, practice_id: 1 }, error: undefined });
      }
      return Promise.resolve({ data: undefined, error: { detail: `unmocked POST ${path}` } });
    });
    renderWithRouter();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Storna" }));
    await user.type(screen.getByLabelText("Motivo dello storno"), "Importo sbagliato");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Storna" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/api/payments/{payment_id}/storna", expect.anything()));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Storna pagamento" })).not.toBeInTheDocument());
  });
});
