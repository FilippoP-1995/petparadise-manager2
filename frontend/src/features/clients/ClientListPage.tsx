import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useClients, useDeactivateClient, type Client } from "./api";

export function ClientListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: clients, isLoading, isError } = useClients({ q, offset });
  const deactivate = useDeactivateClient();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [deactivateTarget, setDeactivateTarget] = useState<Client | null>(null);

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Clienti</h1>
          <div className="sub">Anagrafica clienti.</div>
        </div>
        {/* V1 (clients_page, app.py:15407) non ha una pagina di creazione
            separata - "Aggiungi cliente" e' un form inline in fondo a
            questa stessa pagina, che posta su "/clienti". V2 usa una
            pagina dedicata (ClientFormPage, /clienti/nuovo): stesso form,
            stessi campi, solo instradato diversamente - gap architetturale
            disclosed, non un nuovo dominio. */}
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

      {/* V1 (clients_page, app.py:15407): tabella dentro
          <section class="section"><h2>LISTA CLIENTI</h2><div class="tablebox">
          - non una <table> nuda nel flusso della pagina. */}
      {clients && clients.length > 0 && (
        <section className="section">
          <h2>LISTA CLIENTI</h2>
          <div className="tablebox">
            <table className="premium-table">
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th>Telefono</th>
                  <th>Email</th>
                  <th>Comune</th>
                  {isAdmin && <th></th>}
                </tr>
              </thead>
              <tbody>
                {clients.map((client) => {
                  const name = [client.first_name, client.last_name].filter(Boolean).join(" ") || client.company_name || "Cliente senza nome";
                  return (
                    <tr key={client.id}>
                      <td>
                        <div className="premium-row-lead">
                          <span className="premium-avatar" aria-hidden="true">
                            {(name.trim()[0] || "?").toUpperCase()}
                          </span>
                          <div>
                            <Link to={`/clienti/${client.id}`}>
                              <b>{name}</b>
                            </Link>
                            {client.company_name && (client.first_name || client.last_name) && (
                              <>
                                <br />
                                <small className="sub">{client.company_name}</small>
                              </>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>{client.phone ?? "-"}</td>
                      <td>{client.email ?? "-"}</td>
                      <td>{client.city ?? "-"}</td>
                      {isAdmin && (
                        <td>
                          <button className="btn-ghost" onClick={() => setDeactivateTarget(client)}>
                            Disattiva
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!clients || clients.length < 50} onClick={() => setOffset(offset + 50)}>
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
        title="Disattiva cliente"
        description={deactivateTarget ? `Disattivare ${deactivateTarget.first_name} ${deactivateTarget.last_name}?` : ""}
        confirmLabel="Disattiva"
        danger
        isPending={deactivate.isPending}
      />
    </main>
  );
}
