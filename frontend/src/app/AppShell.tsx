import { Link, Outlet } from "react-router-dom";

import { useAuth } from "@/features/auth/AuthContext";

export function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div className="app-shell">
      <header className="top-nav">
        <span className="brand">Pet Paradise Manager V2</span>
        <nav>
          <Link to="/clienti">Clienti</Link>
          <Link to="/veterinari">Veterinari</Link>
        </nav>
        {user && (
          <div className="user-menu">
            <span>
              {user.display_name} ({user.role})
            </span>
            <button className="btn-ghost" onClick={() => logout()}>
              Esci
            </button>
          </div>
        )}
      </header>
      <Outlet />
    </div>
  );
}
