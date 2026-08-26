import { Link } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useDeactivateVeterinarian, useVeterinarians } from "./api";

export function VeterinarianListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: veterinarians, isLoading, isError } = useVeterinarians({ q, offset });
  const deactivate = useDeactivateVeterinarian();
  const { user } = useAuth();

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Veterinari</h1>
        <Link className="btn" to="/veterinari/nuovo">
          + Nuovo veterinario
        </Link>
      </div>

      <input
        className="search-input"
        placeholder="Cerca per clinica, medico, citta'..."
        defaultValue={q}
        onChange={(e) => setSearch(e.target.value)}
      />

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento dei veterinari.</p>}
      {veterinarians && veterinarians.length === 0 && <p className="empty-state">Nessun veterinario trovato.</p>}

      {veterinarians && veterinarians.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Clinica</th>
              <th>Medico</th>
              <th>Telefono</th>
              <th>Citta'</th>
              <th>Orari</th>
              {user?.role === "admin" && <th></th>}
            </tr>
          </thead>
          <tbody>
            {veterinarians.map((vet) => (
              <tr key={vet.id}>
                <td>{vet.clinic_name ?? "-"}</td>
                <td>{vet.doctor_name ?? "-"}</td>
                <td>{vet.phone ?? "-"}</td>
                <td>{vet.city ?? "-"}</td>
                <td>{vet.hours.length} giorni configurati</td>
                {user?.role === "admin" && (
                  <td>
                    <button
                      className="btn-ghost"
                      onClick={() => {
                        if (confirm(`Disattivare ${vet.clinic_name ?? vet.doctor_name}?`)) {
                          deactivate.mutate(vet.id);
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
        <button disabled={!veterinarians || veterinarians.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
