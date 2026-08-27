import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Practice } from "@/features/practices/api";

import { PracticePaymentsSection } from "./PracticePaymentsSection";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: vi.fn(),
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
