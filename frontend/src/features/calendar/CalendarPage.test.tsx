import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useNavigationType } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CalendarPage } from "./CalendarPage";

const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));

vi.mock("@/shared/api/client", () => ({
  apiClient: {
    GET: (...args: unknown[]) => getMock(...args),
    POST: vi.fn(),
    PUT: vi.fn(),
    DELETE: vi.fn(),
  },
}));

function setupApiMock() {
  getMock.mockImplementation((path: string) => {
    if (path === "/api/pickups") return Promise.resolve({ data: [], error: undefined });
    if (path === "/api/deliveries") return Promise.resolve({ data: [], error: undefined });
    return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
  });
}

// Sonda affiancata a CalendarPage per leggere useNavigationType() sullo
// stesso router - stesso approccio gia' usato in useListQueryParams.test.tsx
// per distinguere REPLACE da PUSH senza dipendere da window.history (non
// affidabile in MemoryRouter).
function NavigationTypeProbe() {
  const navigationType = useNavigationType();
  return <span data-testid="navigation-type">{navigationType}</span>;
}

function renderCalendar() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/calendario?data=2026-08-20"]}>
        <NavigationTypeProbe />
        <CalendarPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CalendarPage - navigazione interna dei giorni usa REPLACE, non PUSH", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("'Giorno precedente' naviga con REPLACE", async () => {
    renderCalendar();
    act(() => screen.getByRole("button", { name: "Giorno precedente" }).click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
  });

  it("'Giorno successivo' naviga con REPLACE", async () => {
    renderCalendar();
    act(() => screen.getByRole("button", { name: "Giorno successivo" }).click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
  });

  it("'Oggi' naviga con REPLACE", async () => {
    renderCalendar();
    act(() => screen.getByRole("button", { name: "Oggi" }).click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
  });

  it("il cambio manuale della data (input date) naviga con REPLACE", async () => {
    renderCalendar();
    const input = screen.getByDisplayValue("2026-08-20");
    act(() => {
      fireEvent.change(input, { target: { value: "2026-08-25" } });
    });
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
  });

  it("piu' navigazioni consecutive tra giorni restano sempre REPLACE (nessuna pila di history interna)", async () => {
    renderCalendar();
    act(() => screen.getByRole("button", { name: "Giorno successivo" }).click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
    act(() => screen.getByRole("button", { name: "Giorno successivo" }).click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
    act(() => screen.getByRole("button", { name: "Giorno precedente" }).click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
  });
});
