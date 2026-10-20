import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

describe("CalendarPage - rendering eventi (dati V2 reali, vista Giorno/Settimana)", () => {
  beforeEach(() => {
    getMock.mockReset();
    getMock.mockImplementation((path: string) => {
      if (path === "/api/pickups") {
        return Promise.resolve({
          data: [
            {
              id: 1,
              pickup_status: "da_ritirare",
              start_at: "2026-08-20T09:30:00Z",
              end_at: "2026-08-20T10:00:00Z",
              pickup_type: "domicilio",
              animals: [{ id: 1, name: "Fido", sort_order: 0 }],
            },
          ],
          error: undefined,
        });
      }
      if (path === "/api/deliveries") {
        return Promise.resolve({
          data: [
            {
              id: 2,
              start_at: "2026-08-20T14:00:00Z",
              end_at: "2026-08-20T14:30:00Z",
              delivery_type: "ambulatorio",
              linked_practice_id: 42,
            },
          ],
          error: undefined,
        });
      }
      return Promise.resolve({ data: undefined, error: { detail: `unmocked path ${path}` } });
    });
  });

  it("mostra le card di Ritiri e Riconsegne del giorno selezionato, con tipo/animale/pratica reali", async () => {
    renderCalendar();
    expect(await screen.findByText("domicilio")).toBeInTheDocument();
    // V1 (calendar_appt_card_html, app.py:9778-9790): il nome animale e'
    // preceduto da un'emoji di specie (qui il generico paw, nessuna specie
    // nel mock) - stessa informazione, testo non piu' esattamente "Fido".
    expect(screen.getByText(/Fido/)).toBeInTheDocument();
    expect(screen.getByText("ambulatorio")).toBeInTheDocument();
    expect(screen.getByText("Pratica #42")).toBeInTheDocument();
    expect(screen.getAllByText("RITIRO")).toHaveLength(1);
    expect(screen.getAllByText("RICONSEGNA")).toHaveLength(1);
  });

  it("la daybar conta gli appuntamenti del giorno selezionato (2: 1 ritiro + 1 riconsegna)", async () => {
    renderCalendar();
    await screen.findByText("domicilio");
    // "20" e' il numero del giorno selezionato nella daybar (2026-08-20).
    const dayCard = screen.getByText("20").closest("button");
    expect(dayCard).not.toBeNull();
    expect(dayCard!.textContent).toContain("2 app.");
  });

  it("il filtro Tipo='Ritiro' nasconde le riconsegne (client-side, stessi dati gia' scaricati)", async () => {
    renderCalendar();
    const user = userEvent.setup();
    await screen.findByText("domicilio");

    const tipoSelect = screen.getByDisplayValue("Tutti i tipi");
    await user.selectOptions(tipoSelect, "pickup");

    expect(screen.getByText("domicilio")).toBeInTheDocument();
    expect(screen.queryByText("ambulatorio")).not.toBeInTheDocument();
  });

  it("il filtro Stato='Ritirato' nasconde un ritiro con stato diverso", async () => {
    renderCalendar();
    const user = userEvent.setup();
    await screen.findByText("domicilio");

    const statoSelect = screen.getByDisplayValue("Tutti gli stati dei ritiri");
    await user.selectOptions(statoSelect, "ritirato");

    // Il ritiro mockato ha pickup_status "da_ritirare", non "ritirato".
    expect(screen.queryByText("domicilio")).not.toBeInTheDocument();
  });
});

describe("CalendarPage - passaggio a vista Mese", () => {
  beforeEach(() => {
    getMock.mockReset();
    setupApiMock();
  });

  it("il pulsante 'Mese' mostra la griglia mensile (42 celle) invece dell'agenda giornaliera", async () => {
    renderCalendar();
    act(() => screen.getByRole("button", { name: "Mese" }).click());
    // Agosto 2026 inizia di sabato -> la griglia parte dal lunedi' 27/07.
    expect(await screen.findAllByText(/Agosto/)).not.toHaveLength(0);
    const cells = document.querySelectorAll(".calendar-month-v2-cell");
    expect(cells).toHaveLength(42);
  });

  it("cliccare una cella del mese seleziona il giorno senza cambiare vista", async () => {
    renderCalendar();
    act(() => screen.getByRole("button", { name: "Mese" }).click());
    await screen.findAllByText(/Agosto/);
    const cell25 = [...document.querySelectorAll(".calendar-month-v2-cell")].find(
      (el) => el.querySelector(".calendar-month-v2-num")?.textContent === "25",
    );
    expect(cell25).toBeTruthy();
    act(() => (cell25 as HTMLElement).click());
    expect(screen.getByRole("button", { name: "Mese" })).toHaveClass("active");
  });
});
