import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import { useCompanyLocationsAdmin, useDeactivateCompanyLocation, type CompanyLocation } from "./api";

export function CompanyLocationListPage() {
  const { data: locations, isLoading, isError } = useCompanyLocationsAdmin();
  const deactivate = useDeactivateCompanyLocation();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [deactivateTarget, setDeactivateTarget] = useState<CompanyLocation | null>(null);

  return (
    // V1 (route_locations_page, app.py:11710-11730, servita su
    // "/percorso-giornaliero/sedi"): <main class="wrap"><div class="titlebar">
    // <div><h1>Sedi aziendali</h1><p class="sub">...</p></div>... e la lista
    // vive in <section class="section"><h2>Sedi configurate</h2>{righe}
    // </section>, ogni riga in un <div/form class="tablebox"> - non una
    // <table class="data-table"> nuda. Qui si riusa il pattern reale
    // .section/.tablebox gia' corretto in ClientListPage/
    // VeterinarianListPage (.premium-table dentro .tablebox), che si adatta
    // meglio alla vista tabellare voluta da V2 rispetto alle card
    // ripetute di V1 - stessi contenitori reali, solo table invece di righe
    // sciolte, dato lo split lista/form gia' disclosed altrove.
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Sedi aziendali</h1>
          {/* Testo V1 (app.py:11729) adattato: "Punti di partenza e arrivo
              disponibili nel Percorso giornaliero" non si applica ancora a
              V2 (dominio Percorso Giornaliero non costruito, vedi
              CompanyLocationFormPage.tsx) - sostituito con una descrizione
              generica del dominio realmente presente in V2. */}
          <div className="sub">Anagrafica delle sedi aziendali.</div>
        </div>
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
        <section className="section">
          <h2>Sedi configurate</h2>
          <div className="tablebox">
            <table className="premium-table">
              <thead>
                <tr>
                  <th>Nome</th>
                  <th>Impianto di cremazione</th>
                  {isAdmin && <th></th>}
                </tr>
              </thead>
              <tbody>
                {locations.map((location) => (
                  <tr key={location.id}>
                    <td>
                      {isAdmin ? <Link to={`/sedi/${location.id}/modifica`}><b>{location.name}</b></Link> : <b>{location.name}</b>}
                      {/* Stessa convenzione gia' stabilita in
                          UrnListPage/UrnDetailPage: badge "status-inactive"
                          mostrato solo quando disattivata, mai un badge
                          "status-active" per il caso normale (classe mai
                          usata altrove nel frontend). */}
                      {!location.active && (
                        <>
                          <br />
                          <span className="badge status-inactive">Disattivata</span>
                        </>
                      )}
                    </td>
                    <td>{location.has_cremation_plant ? "Si" : "No"}</td>
                    {isAdmin && (
                      <td>
                        {location.active && (
                          <button className="btn-ghost" disabled={deactivate.isPending} onClick={() => setDeactivateTarget(location)}>
                            Disattiva
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <ConfirmModal
        open={deactivateTarget != null}
        onClose={() => setDeactivateTarget(null)}
        onConfirm={() => {
          if (deactivateTarget) deactivate.mutate(deactivateTarget.id);
          setDeactivateTarget(null);
        }}
        title="Disattiva sede"
        description={deactivateTarget ? `Disattivare la sede "${deactivateTarget.name}"?` : ""}
        confirmLabel="Disattiva"
        danger
        isPending={deactivate.isPending}
      />
    </main>
  );
}
