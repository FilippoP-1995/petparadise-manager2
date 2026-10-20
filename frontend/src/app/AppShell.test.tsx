import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/features/auth/AuthContext";

import { AppShell } from "./AppShell";

const { fetchCurrentUserMock } = vi.hoisted(() => ({ fetchCurrentUserMock: vi.fn() }));

vi.mock("@/features/auth/api", () => ({
  login: vi.fn(),
  logout: vi.fn(),
  fetchCurrentUser: fetchCurrentUserMock,
}));

// Sonda minima al posto della vera PracticeListPage: mostra solo il
// parametro "q" ricevuto, cosi' il test verifica la stessa identica cosa
// che riceverebbe la pagina reale (useSearchParams), senza montare tutto
// l'elenco pratiche e le sue chiamate API.
function PracticesProbe() {
  const [params] = useSearchParams();
  return <div data-testid="practices-q">{params.get("q") ?? ""}</div>;
}

function renderShell() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<div>Dashboard placeholder</div>} />
              <Route path="/pratiche" element={<PracticesProbe />} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell - ricerca header desktop", () => {
  beforeEach(() => {
    fetchCurrentUserMock.mockReset().mockResolvedValue({ id: 1, username: "admin", display_name: "Admin", role: "admin" });
  });

  it("invia il testo digitato come parametro 'q' verso /pratiche, stesso filtro gia' usato dall'elenco", async () => {
    const user = userEvent.setup();
    renderShell();

    const input = await screen.findByRole("searchbox", { name: /ricerca pratiche/i });
    await user.type(input, "Bianchi");
    await user.keyboard("{Enter}");

    expect(await screen.findByTestId("practices-q")).toHaveTextContent("Bianchi");
  });

  it("naviga a /pratiche senza parametro 'q' se la ricerca e' vuota", async () => {
    const user = userEvent.setup();
    renderShell();

    const input = await screen.findByRole("searchbox", { name: /ricerca pratiche/i });
    await user.click(input);
    await user.keyboard("{Enter}");

    expect(await screen.findByTestId("practices-q")).toHaveTextContent("");
  });
});
