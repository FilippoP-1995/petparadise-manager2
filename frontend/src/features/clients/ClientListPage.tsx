import { Link } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useClients, useDeactivateClient } from "./api";

export function ClientListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: clients, isLoading, isError } = useClients({ q, offset });
  const deactivate = useDeactivateClient();
  const { user } = useAuth();

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Clienti</h1>
        <Link className="btn" to="/clienti/nuovo">
          + Nuovo cliente
        </Link>
      </div>

      <input
        className="search-input"
        placeholder="Cerca per nome, telefono, email..."
        defaultValue={q}
        onChange={(e) => setSearch(e.target.value)}
      />

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento dei clienti.</p>}

      {clients && clients.length === 0 && <p className="empty-state">Nessun cliente trovato.</p>}

      {clients && clients.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Nome</th>
              <th>Telefono</th>
              <th>Citta'</th>
              {user?.role === "admin" && <th></th>}
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => (
              <tr key={client.id}>
                <td>{[client.first_name, client.last_name].filter(Boolean).join(" ") || client.company_name}</td>
                <td>{client.phone ?? "-"}</td>
                <td>{client.city ?? "-"}</td>
                {user?.role === "admin" && (
                  <td>
                    <button
                      className="btn-ghost"
                      onClick={() => {
                        if (confirm(`Disattivare ${client.first_name} ${client.last_name}?`)) {
                          deactivate.mutate(client.id);
                        }
                      }}
                    >
                      Disattiva
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!clients || clients.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
