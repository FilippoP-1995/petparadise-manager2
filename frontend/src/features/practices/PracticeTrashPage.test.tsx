import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PracticeTrashPage } from "./PracticeTrashPage";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: (...args: unknown[]) => postMock(...args),
    PUT: vi.fn(),
    DELETE: vi.fn(),
  },
}));

const TRASHED_PRACTICE = {
  id: 8,
  practice_number: "COL-000008",
  original_practice_number: null,
  owner_snapshot: { first_name: "Anna", last_name: "Bianchi" },
  animals: [{ id: 1, sort_order: 0, name: "Fido", species: "Cane" }],
  veterinarian_id: null,
  deleted_at: "2026-09-01T10:00:00Z",
  created_at: "2026-08-20T09:00:00Z",
};

function setupApiMock(practices: unknown[] = [TRASHED_PRACTICE]) {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/practices/trashed") return Promise.resolve({ data: practices, error: undefined });
    if (path === "/api/veterinarians") return Promise.resolve({ data: [], error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked GET ${path}` } });
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/cestino"]}>
        <Routes>
          <Route path="/pratiche" element={<div>Elenco pratiche</div>} />
          <Route path="/cestino" element={<PracticeTrashPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PracticeTrashPage - Cestino (V1: trash_page, app.py:18307)", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("mostra 'Il Cestino e' vuoto.' quando non ci sono pratiche cestinate", async () => {
    setupApiMock([]);
    renderPage();
    expect(await screen.findByText("Il Cestino e' vuoto.")).toBeInTheDocument();
  });

  it("elenca le pratiche cestinate con animale e speditore reali", async () => {
    setupApiMock();
    renderPage();
    expect(await screen.findByText("COL-000008")).toBeInTheDocument();
    expect(screen.getByText("Fido")).toBeInTheDocument();
    expect(screen.getByText("Anna Bianchi")).toBeInTheDocument();
  });

  it("Ripristina chiede conferma e poi chiama /restore per l'id corretto", async () => {
    setupApiMock();
    postMock.mockResolvedValue({ data: { ...TRASHED_PRACTICE, deleted_at: null }, error: undefined });
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Ripristina" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Ripristinare la pratica COL-000008/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Ripristina" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/api/practices/{practice_id}/restore", expect.objectContaining({ params: { path: { practice_id: 8 } } })),
    );
  });
});
