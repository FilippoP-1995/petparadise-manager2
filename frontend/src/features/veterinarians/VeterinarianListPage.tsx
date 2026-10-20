import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";
import { useListQueryParams } from "@/shared/useListQueryParams";

import "../clients/clientsVeterinarians.css";
import { useDeactivateVeterinarian, useVeterinarians, type Veterinarian } from "./api";

export function VeterinarianListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: veterinarians, isLoading, isError } = useVeterinarians({ q, offset });
  const deactivate = useDeactivateVeterinarian();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [deactivateTarget, setDeactivateTarget] = useState<Veterinarian | null>(null);

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Veterinari</h1>
          <div className="sub">Anagrafiche strutture veterinarie e buoni.</div>
        </div>
        {/* V1 (veterinarians_page, app.py:15263) non ha una pagina di
            creazione separata - "Aggiungi veterinario" e' un form inline
            in fondo a questa stessa pagina. V2 usa una pagina dedicata
            (/veterinari/nuovo): stesso form, solo instradato diversamente. */}
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

      {/* V1 (veterinarians_page, app.py:15263): tabella dentro
          <section class="section"><h2>LISTA VETERINARI</h2>
          <div class="tablebox"><table> - la <table> di V1 non ha classe
          propria (regola CSS di base table/th/td, non .premium-table),
          replicata qui con .vet-table (vedi clientsVeterinarians.css) per
          restare fedele ai valori reali invece di riusare .data-table
          (che duplicherebbe sfondo/bordo/raggio gia' dati da .tablebox).
          La colonna "Buoni" di V1 (badge Maturati/Usati,
          veterinarian_vouchers) non ha equivalente in V2 - nessun dominio
          buoni-veterinario nel backend V2 (solo un campo voucher su
          Practice, non una vera gestione buoni per veterinario) -
          sostituita con un dato reale gia' disponibile (giorni orario
          configurati), mai inventata. */}
      {veterinarians && veterinarians.length > 0 && (
        <section className="section">
          <h2>LISTA VETERINARI</h2>
          <div className="tablebox">
            <table className="vet-table">
              <thead>
                <tr>
                  <th>Veterinario</th>
                  <th>Indirizzo</th>
                  <th>Telefono</th>
                  <th>Orari</th>
                  {isAdmin && <th></th>}
                </tr>
              </thead>
              <tbody>
                {veterinarians.map((vet) => (
                  <tr key={vet.id}>
                    <td>
                      <Link to={`/veterinari/${vet.id}`}>
                        <b>{vet.short_name || vet.clinic_name}</b>
                      </Link>
                      {vet.short_name && vet.clinic_name && (
                        <>
                          <br />
                          <small className="sub">{vet.clinic_name}</small>
                        </>
                      )}
                    </td>
                    <td>
                      {vet.address ?? "-"}
                      {vet.city && (
                        <>
                          <br />
                          <small className="sub">{vet.city}</small>
                        </>
                      )}
                    </td>
                    <td>{vet.phone ?? "-"}</td>
                    <td>{vet.hours.length} giorni configurati</td>
                    {isAdmin && (
                      <td>
                        <button className="btn-ghost" onClick={() => setDeactivateTarget(vet)}>
                          Disattiva
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!veterinarians || veterinarians.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>

      <ConfirmModal
        open={deactivateTarget != null}
        onClose={() => setDeactivateTarget(null)}
        onConfirm={() => {
          if (deactivateTarget) deactivate.mutate(deactivateTarget.id);
          setDeactivateTarget(null);
        }}
        title="Disattiva veterinario"
        description={deactivateTarget ? `Disattivare ${deactivateTarget.clinic_name ?? deactivateTarget.doctor_name}?` : ""}
        confirmLabel="Disattiva"
        danger
        isPending={deactivate.isPending}
      />
    </main>
  );
}
