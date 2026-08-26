import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthContext";
import { LoginPage } from "./LoginPage";

const { loginMock, fetchCurrentUserMock } = vi.hoisted(() => ({
  loginMock: vi.fn(),
  fetchCurrentUserMock: vi.fn(),
}));

vi.mock("./api", () => ({
  login: loginMock,
  logout: vi.fn(),
  fetchCurrentUser: fetchCurrentUserMock,
}));

function Wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient();
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>{children}</AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    loginMock.mockReset();
    fetchCurrentUserMock.mockReset().mockResolvedValue(null);
  });

  it("mostra un errore se le credenziali sono sbagliate, senza svuotare i campi", async () => {
    loginMock.mockRejectedValueOnce(new Error("Credenziali non valide"));
    const user = userEvent.setup();

    render(<LoginPage />, { wrapper: Wrapper });

    await user.type(screen.getByLabelText("Utente"), "admin");
    await user.type(screen.getByLabelText("Password"), "sbagliata");
    await user.click(screen.getByRole("button", { name: "Accedi" }));

    await waitFor(() => expect(screen.getByText("Credenziali non valide.")).toBeInTheDocument());
    // requisito esplicito del progetto: mai svuotare il form dopo un errore
    expect(screen.getByLabelText("Utente")).toHaveValue("admin");
  });

  it("chiama login con le credenziali inserite", async () => {
    loginMock.mockResolvedValueOnce({ user_id: 1, display_name: "Admin", role: "admin" });
    const user = userEvent.setup();

    render(<LoginPage />, { wrapper: Wrapper });

    await user.type(screen.getByLabelText("Utente"), "admin");
    await user.type(screen.getByLabelText("Password"), "segreta");
    await user.click(screen.getByRole("button", { name: "Accedi" }));

    await waitFor(() => expect(loginMock).toHaveBeenCalledWith("admin", "segreta"));
  });
});
