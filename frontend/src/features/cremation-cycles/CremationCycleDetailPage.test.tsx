import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CremationCycleDetailPage } from "./CremationCycleDetailPage";

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

const CYCLE = {
  id: 1,
  status: "in_attesa",
  cycle_date: "2026-09-11",
  planned_start: "09:00:00",
  planned_end: "10:00:00",
  completed_at: null,
  cremation_location_id: null,
  sort_order: 0,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  animals: [{ id: 10, name: "Fido", species: "Cane", practice_id: 99 }],
};

const ELIGIBLE_ANIMALS = [{ id: 20, name: "Rex", species: "Cane", practice_id: 55 }];

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/cremation-cycles/{cycle_id}") return Promise.resolve({ data: CYCLE, error: undefined });
    if (path === "/api/cremation-cycles/eligible-animals") return Promise.resolve({ data: ELIGIBLE_ANIMALS, error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
  });
}

// Stub minimale della pagina Pratica: espone solo un link "Indietro" che
// usa useNavigate(-1), lo stesso meccanismo di un vero tasto Indietro del
// browser (POP), per restare nel router dichiarativo (<MemoryRouter>) e
// non nella modalita' "data router" di createMemoryRouter/RouterProvider,
// che in questo ambiente di test (vitest+jsdom+undici) fa fallire
// qualunque navigazione con un errore di compatibilita' su AbortSignal -
// non un problema del codice applicativo, solo dell'ambiente di test.
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
      <MemoryRouter initialEntries={["/cicli-cremazione/1"]}>
        <Routes>
          <Route path="/cicli-cremazione/:cycleId" element={<CremationCycleDetailPage />} />
          <Route path="/pratiche/:practiceId" element={<PraticaStub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CremationCycleDetailPage - continuita' di navigazione (selectedAnimalId)", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("stato iniziale: nessun animale selezionato senza il parametro nell'URL", async () => {
    renderWithRouter();
    const select = await screen.findByRole("combobox");
    expect(select).toHaveValue("");
  });

  it("la selezione dell'animale sopravvive a una navigazione verso la Pratica collegata seguita da Indietro", async () => {
    renderWithRouter();
    const user = userEvent.setup();

    await screen.findByRole("option", { name: "Rex - Pratica #55" });
    const select = screen.getByRole("combobox");
    await user.selectOptions(select, "20");
    await waitFor(() => expect(select).toHaveValue("20"));

    await user.click(screen.getByRole("link", { name: "#99" }));
    await waitFor(() => expect(screen.getByText("Pratica stub")).toBeInTheDocument());

    await user.click(screen.getByRole("link", { name: "Indietro" }));

    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("20"));
  });
});
