import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PracticeDetailPage } from "./PracticeDetailPage";

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
  id: 5,
  practice_number: "CR-000005",
  status: "ritirato",
  request_origin: "Privato",
  service_type: "Cremazione singola",
  destination_branch_id: 7,
  veterinarian_id: null,
  collaborator_id: null,
  collaborator_billing_status: "da_fatturare",
  computed_total_override_cents: null,
  computed_total_override_reason: null,
  line_items_total_cents: 10000,
  notes: null,
  tags: [],
  owner_snapshot: { first_name: "Mario", last_name: "Rossi", phone: "333123456" },
  animals: [{ id: 1, name: "Fido", species: "Cane", age_years: 5, age_months: null, estimated_weight_grams: 12000, sort_order: 0 }],
  created_at: "2026-08-10T09:00:00Z",
};

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/practices/{practice_id}") return Promise.resolve({ data: PRACTICE, error: undefined });
    if (path === "/api/payments/practice/{practice_id}/riconciliazione") {
      return Promise.resolve({
        data: {
          effective_total_cents: 10000,
          paid_w_cents: 0,
          paid_d_cents: 0,
          paid_collaboratori_cents: 0,
          paid_total_cents: 0,
          residual_cents: 10000,
          status: "non_pagata",
        },
        error: undefined,
      });
    }
    if (path === "/api/payments") return Promise.resolve({ data: [], error: undefined });
    if (path === "/api/invoices") return Promise.resolve({ data: [], error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/pratiche/5"]}>
        <Routes>
          <Route path="/pratiche" element={<div>Elenco pratiche</div>} />
          <Route path="/pratiche/:practiceId" element={<PracticeDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PracticeDetailPage - modal 'Sposta nel Cestino' (V1: popup, non window.confirm)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
    setupApiMock();
  });

  it("il bottone apre un modal di conferma invece di window.confirm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sposta nel Cestino" }));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(await screen.findByText(/Cestinare la pratica CR-000005/)).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("'Annulla' chiude il modal senza cestinare la pratica", async () => {
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sposta nel Cestino" }));
    await screen.findByText(/Cestinare la pratica CR-000005/);
    await user.click(screen.getByRole("button", { name: "Annulla" }));

    expect(screen.queryByText(/Cestinare la pratica CR-000005/)).not.toBeInTheDocument();
    expect(postMock).not.toHaveBeenCalledWith("/api/practices/{practice_id}/trash", expect.anything());
  });

  it("confermando nel modal la pratica viene cestinata e si torna all'elenco", async () => {
    postMock.mockImplementation((path: string) => {
      if (path === "/api/practices/{practice_id}/trash") return Promise.resolve({ data: { id: 5 }, error: undefined });
      return Promise.resolve({ data: undefined, error: { detail: `unmocked POST ${path}` } });
    });
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sposta nel Cestino" }));
    await screen.findByText(/Cestinare la pratica CR-000005/);
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Sposta nel Cestino" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/api/practices/{practice_id}/trash", expect.anything()));
    expect(await screen.findByText("Elenco pratiche")).toBeInTheDocument();
  });
});
