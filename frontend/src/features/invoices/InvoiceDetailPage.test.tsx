import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InvoiceDetailPage } from "./InvoiceDetailPage";

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

const INVOICE = {
  id: 1,
  invoice_number: "F-2026-001",
  practice_id: 99,
  practice_number_snapshot: "P-99",
  invoice_date: "2026-08-20",
  channel: "W",
  total_amount_cents: 10000,
};

const RECON = {
  status: "parziale",
  total_amount_cents: 10000,
  paid_cents: 5000,
  residual_cents: 5000,
};

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/invoices/{invoice_id}") return Promise.resolve({ data: INVOICE, error: undefined });
    if (path === "/api/invoices/{invoice_id}/riconciliazione") return Promise.resolve({ data: RECON, error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
  });
}

function PraticaStub() {
  return (
    <div>
      <p>Pratica stub</p>
      <Link to={-1 as unknown as string}>Indietro</Link>
    </div>
  );
}

function renderWithRouter() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/fatture/1"]}>
        <Routes>
          <Route path="/fatture/:invoiceId" element={<InvoiceDetailPage />} />
          <Route path="/pratiche/:practiceId" element={<PraticaStub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("InvoiceDetailPage - continuita' di navigazione (form correzione totale)", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("il form di correzione resta chiuso all'apertura della pagina", async () => {
    renderWithRouter();
    await screen.findByText("F-2026-001");
    expect(screen.queryByPlaceholderText(/Attuale:/)).not.toBeInTheDocument();
  });

  it("il form di correzione aperto sopravvive a una navigazione verso la Pratica collegata seguita da Indietro", async () => {
    renderWithRouter();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Correggi totale fattura" }));
    expect(screen.getByPlaceholderText(/Attuale:/)).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "P-99" }));
    await waitFor(() => expect(screen.getByText("Pratica stub")).toBeInTheDocument());

    await user.click(screen.getByRole("link", { name: "Indietro" }));

    await waitFor(() => expect(screen.getByPlaceholderText(/Attuale:/)).toBeInTheDocument());
  });
});
