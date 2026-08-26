import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { useListQueryParams } from "./useListQueryParams";

function Probe() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  return (
    <div>
      <span data-testid="q">{q}</span>
      <span data-testid="offset">{offset}</span>
      <button onClick={() => setSearch("mario")}>cerca</button>
      <button onClick={() => setOffset(50)}>pagina successiva</button>
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
});
