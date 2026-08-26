import { Link, Outlet } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";

export function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div className="app-shell">
      <header className="top-nav">
        <span className="brand">Pet Paradise Manager V2</span>
        <nav>
          <Link to="/pratiche">Pratiche</Link>
          <Link to="/ritiri">Ritiri</Link>
          <Link to="/riconsegne">Riconsegne</Link>
          <Link to="/cicli-cremazione">Cicli di cremazione</Link>
          <Link to="/fatture">Fatture</Link>
          <Link to="/clienti">Clienti</Link>
          <Link to="/veterinari">Veterinari</Link>
          <Link to="/sedi">Sedi</Link>
          <Link to="/catalogo-urne">Catalogo Urne</Link>
          <Link to="/prodotti">Prodotti</Link>
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
