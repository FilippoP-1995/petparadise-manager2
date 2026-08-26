import { Link } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";

import { useCompanyLocationsAdmin, useDeactivateCompanyLocation } from "./api";

export function CompanyLocationListPage() {
  const { data: locations, isLoading, isError } = useCompanyLocationsAdmin();
  const deactivate = useDeactivateCompanyLocation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Sedi aziendali</h1>
        {isAdmin && (
          <Link className="btn" to="/sedi/nuova">
            + Nuova sede
          </Link>
        )}
      </div>
      {!isAdmin && <p className="sub">Solo gli amministratori possono modificare le sedi aziendali.</p>}

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento delle sedi.</p>}
      {locations && locations.length === 0 && <p className="empty-state">Nessuna sede configurata.</p>}

      {locations && locations.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Nome</th>
              <th>Impianto di cremazione</th>
              <th>Stato</th>
              {isAdmin && <th></th>}
            </tr>
          </thead>
          <tbody>
            {locations.map((location) => (
              <tr key={location.id}>
                <td>{isAdmin ? <Link to={`/sedi/${location.id}/modifica`}>{location.name}</Link> : location.name}</td>
                <td>{location.has_cremation_plant ? "Si" : "No"}</td>
                <td>
                  <span className={`badge ${location.active ? "status-active" : "status-inactive"}`}>
                    {location.active ? "Attiva" : "Disattivata"}
                  </span>
                </td>
                {isAdmin && (
                  <td>
                    {location.active && (
                      <button
                        className="btn-ghost"
                        disabled={deactivate.isPending}
                        onClick={() => {
                          if (confirm(`Disattivare la sede "${location.name}"?`)) deactivate.mutate(location.id);
                        }}
                      >
                        Disattiva
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
