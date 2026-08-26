import { useNavigate, useParams } from "react-router-dom";

import { useDeactivateUrn, useUrn, useUrnMovements } from "./api";

function money(cents: number) {
  return (cents / 100).toLocaleString("it-IT", { style: "currency", currency: "EUR" });
}

export function UrnDetailPage() {
  const { urnId } = useParams();
  const navigate = useNavigate();
  const id = Number(urnId);
  const { data: urn, isLoading, isError } = useUrn(id);
  const { data: movements } = useUrnMovements(id);
  const deactivate = useDeactivateUrn();

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !urn) return <p className="error-banner">Articolo non trovato.</p>;

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>{urn.name}</h1>
        <span className={`badge ${urn.active ? "status-active" : "status-inactive"}`}>
          {urn.active ? "Attivo" : "Disattivato"}
        </span>
      </div>

      <div className="card">
        <p>
          <strong>Codice interno:</strong> {urn.internal_code}
        </p>
        <p>
          <strong>Categoria:</strong> {urn.category}
        </p>
        <p>
          <strong>Materiale:</strong> {urn.material || "-"}
        </p>
        <p>
          <strong>Prezzo:</strong> {money(urn.price_cents)}
        </p>
        <p>
          <strong>Quantita:</strong> {urn.quantity} (soglia scorte basse: {urn.low_stock_threshold})
        </p>
        {urn.notes && (
          <p>
            <strong>Note:</strong> {urn.notes}
          </p>
        )}
      </div>

      <div className="card">
        <h2>Storico movimenti</h2>
        {(!movements || movements.length === 0) && <p className="empty-state">Nessun movimento registrato.</p>}
        {movements && movements.length > 0 && (
          <div className="timeline">
            {movements.map((m) => (
              <div className="event" key={m.id}>
                <b>{m.movement_type}</b> · {m.quantity_delta >= 0 ? "+" : ""}
                {m.quantity_delta} ({m.old_quantity} → {m.new_quantity})
                <br />
                <small className="sub">
                  {new Date(m.created_at).toLocaleString("it-IT")}
                  {m.note ? ` · ${m.note}` : ""}
                </small>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="actions">
        <button className="btn" onClick={() => navigate(`/catalogo-urne/${urn.id}/modifica`)}>
          Modifica
        </button>
        {urn.active && (
          <button
            className="btn-ghost"
            disabled={deactivate.isPending}
            onClick={() => {
              if (confirm("Rimuovere questo articolo dal catalogo?")) deactivate.mutate(urn.id);
            }}
          >
            Rimuovi dal catalogo
          </button>
        )}
        <button className="btn-ghost" onClick={() => navigate("/catalogo-urne")}>
          Torna al catalogo
        </button>
      </div>
    </main>
  );
}
