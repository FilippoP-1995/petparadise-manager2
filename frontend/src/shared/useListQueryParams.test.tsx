import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, useNavigationType, useSearchParams } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useListQueryParams } from "./useListQueryParams";

function Probe() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const navigationType = useNavigationType();
  // Stesso pattern di composizione usato da Pratiche/Ritiri/Cicli: un
  // secondo useSearchParams "grezzo" sullo stesso URL, per un filtro non
  // gestito da useListQueryParams (qui simulato con "status").
  const [searchParams] = useSearchParams();
  const status = searchParams.get("status") ?? "";
  return (
    <div>
      <span data-testid="q">{q}</span>
      <span data-testid="offset">{offset}</span>
      <span data-testid="status">{status}</span>
      <span data-testid="navigation-type">{navigationType}</span>
      <button onClick={() => setSearch("mario")}>cerca</button>
      <button onClick={() => setSearch("m")}>cerca-m</button>
      <button onClick={() => setSearch("ma")}>cerca-ma</button>
      <button onClick={() => setSearch("mar")}>cerca-mar</button>
      <button onClick={() => setOffset(50)}>pagina successiva</button>
      <button onClick={() => setOffset(100)}>pagina 3</button>
    </div>
  );
}

describe("useListQueryParams", () => {
  it("legge lo stato iniziale dall'URL", () => {
    render(
      <MemoryRouter initialEntries={["/clienti?q=rossi&offset=50"]}>
        <Probe />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("q")).toHaveTextContent("rossi");
    expect(screen.getByTestId("offset")).toHaveTextContent("50");
  });

  it("una nuova ricerca aggiorna l'URL e azzera la paginazione", () => {
    render(
      <MemoryRouter initialEntries={["/clienti?q=vecchio&offset=50"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("cerca").click());
    expect(screen.getByTestId("q")).toHaveTextContent("mario");
    expect(screen.getByTestId("offset")).toHaveTextContent("0");
  });

  it("cambiare pagina preserva la ricerca attiva", () => {
    render(
      <MemoryRouter initialEntries={["/clienti?q=rossi"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("pagina successiva").click());
    expect(screen.getByTestId("q")).toHaveTextContent("rossi");
    expect(screen.getByTestId("offset")).toHaveTextContent("50");
  });

  // Audit di navigazione (Fase 2): "Indietro" deve uscire dalla pagina,
  // non annullare un filtro un passo alla volta - verificato qui
  // direttamente sul tipo di navigazione ricevuto dal router
  // (useNavigationType), non su window.history (non affidabile in
  // MemoryRouter) - stesso approccio suggerito come fallback.

  it("setSearch naviga con REPLACE, non PUSH", () => {
    render(
      <MemoryRouter initialEntries={["/clienti"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("cerca").click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
  });

  it("setOffset naviga con REPLACE, non PUSH", () => {
    render(
      <MemoryRouter initialEntries={["/clienti"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("pagina successiva").click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
  });

  it("piu' ricerche consecutive (simula la digitazione carattere per carattere) restano sempre REPLACE", () => {
    render(
      <MemoryRouter initialEntries={["/clienti"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("cerca-m").click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
    act(() => screen.getByText("cerca-ma").click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
    act(() => screen.getByText("cerca-mar").click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
    expect(screen.getByTestId("q")).toHaveTextContent("mar");
  });

  it("piu' cambi pagina consecutivi restano sempre REPLACE (nessuna pila di history interna)", () => {
    render(
      <MemoryRouter initialEntries={["/clienti"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("pagina successiva").click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
    act(() => screen.getByText("pagina 3").click());
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");
    expect(screen.getByTestId("offset")).toHaveTextContent("100");
  });

  it("setOffset preserva un parametro estraneo gia' presente nell'URL (es. status di un filtro composto)", () => {
    render(
      <MemoryRouter initialEntries={["/pratiche?status=ritirato"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("pagina successiva").click());
    expect(screen.getByTestId("offset")).toHaveTextContent("50");
    expect(screen.getByTestId("status")).toHaveTextContent("ritirato");
  });

  it("setSearch preserva un parametro estraneo gia' presente nell'URL", () => {
    render(
      <MemoryRouter initialEntries={["/pratiche?status=ritirato"]}>
        <Probe />
      </MemoryRouter>,
    );
    act(() => screen.getByText("cerca").click());
    expect(screen.getByTestId("q")).toHaveTextContent("mario");
    expect(screen.getByTestId("status")).toHaveTextContent("ritirato");
  });
});
