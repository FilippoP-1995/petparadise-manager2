import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PickupDetailPage } from "./PickupDetailPage";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: vi.fn(),
    PUT: vi.fn(),
    DELETE: vi.fn(),
  },
}));

const PICKUP = {
  id: 1,
  pickup_type: "domicilio",
  pickup_status: "ritirato",
  start_at: "2026-08-20T10:00:00Z",
  linked_practice_id: null,
  animals: [{ id: 5, name: "Fido" }],
};

const LOCATIONS = [
  { id: 1, name: "Sede Nord" },
  { id: 2, name: "Sede Sud" },
];

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/pickups/{pickup_id}") return Promise.resolve({ data: PICKUP, error: undefined });
    if (path === "/api/references/company-locations") return Promise.resolve({ data: LOCATIONS, error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
  });
}

// Stub di un'altra pagina qualunque dell'app, raggiunta ad esempio tramite
// la navigazione condivisa (header/bottom nav) - il contratto da
// verificare e' che lo smontaggio/rimontaggio di PickupDetailPage non
// perda lo stato, a prescindere dalla destinazione intermedia.
function AltroveStub() {
  return (
    <div>
      <p>Altrove stub</p>
      <Link to={-1 as unknown as string}>Indietro</Link>
    </div>
  );
}

function renderWithRouter() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/ritiri/1"]}>
        <Link to="/altrove">Vai altrove</Link>
        <Routes>
          <Route path="/ritiri/:pickupId" element={<PickupDetailPage />} />
          <Route path="/altrove" element={<AltroveStub />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PickupDetailPage - continuita' di navigazione (form creazione pratica + sede)", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("il form di creazione pratica resta chiuso all'apertura della pagina", async () => {
    renderWithRouter();
    await screen.findByRole("button", { name: "Crea pratica" });
    expect(screen.queryByText("Seleziona sede...")).not.toBeInTheDocument();
  });

  it("form aperto e sede selezionata sopravvivono a una navigazione seguita da Indietro", async () => {
    renderWithRouter();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Crea pratica" }));
    const select = screen.getByRole("combobox");
    await user.selectOptions(select, "2");
    await waitFor(() => expect(select).toHaveValue("2"));

    await user.click(screen.getByRole("link", { name: "Vai altrove" }));
    await waitFor(() => expect(screen.getByText("Altrove stub")).toBeInTheDocument());

    await user.click(screen.getByRole("link", { name: "Indietro" }));

    await waitFor(() => expect(screen.getByRole("combobox")).toHaveValue("2"));
    expect(screen.getByRole("button", { name: "Conferma creazione" })).toBeInTheDocument();
  });
});
