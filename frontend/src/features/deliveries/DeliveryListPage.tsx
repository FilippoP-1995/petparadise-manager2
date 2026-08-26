import { Link } from "react-router-dom";

import { useListQueryParams } from "@/shared/useListQueryParams";

import { useDeliveries } from "./api";

export function DeliveryListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: deliveries, isLoading, isError } = useDeliveries({ q, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Riconsegne</h1>
        <Link className="btn" to="/riconsegne/nuova">
          + Nuova riconsegna
        </Link>
      </div>

      <input
        className="search-input"
        placeholder="Cerca..."
        defaultValue={q}
        onChange={(e) => setSearch(e.target.value)}
      />

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento delle riconsegne.</p>}
      {deliveries && deliveries.length === 0 && <p className="empty-state">Nessuna riconsegna trovata.</p>}

      {deliveries && deliveries.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Data</th>
              <th>Modalita'</th>
              <th>Pagamento preliminare</th>
              <th>Pratica</th>
            </tr>
          </thead>
          <tbody>
            {deliveries.map((delivery) => (
              <tr key={delivery.id}>
                <td>
                  <Link to={`/riconsegne/${delivery.id}`}>{new Date(delivery.start_at).toLocaleString("it-IT")}</Link>
                </td>
                <td>{delivery.delivery_type}</td>
                <td>
                  {delivery.preliminary_payment_status ?? "-"}
                  {delivery.preliminary_payment_amount != null && ` (${(delivery.preliminary_payment_amount / 100).toFixed(2)} €)`}
                </td>
                <td>{delivery.linked_practice_id ? `#${delivery.linked_practice_id}` : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!deliveries || deliveries.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
