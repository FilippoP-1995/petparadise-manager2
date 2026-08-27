import { Link, useSearchParams } from "react-router-dom";

import { useListQueryParams } from "@/shared/useListQueryParams";

import { usePickups } from "./api";

const STATUS_LABELS: Record<string, string> = {
  da_confermare: "Da confermare",
  da_ritirare: "Da ritirare",
  ritirato: "Ritirato",
  annullato: "Annullato",
};

export function PickupListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get("status") ?? "";

  function setStatus(value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("status", value);
        else next.delete("status");
        next.delete("offset");
        return next;
      },
      { replace: true },
    );
  }

  const { data: pickups, isLoading, isError } = usePickups({ q, status: status || undefined, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Ritiri</h1>
        <Link className="btn" to="/ritiri/nuovo">
          + Nuovo ritiro
        </Link>
      </div>

      <div className="field-row">
        <input
          className="search-input"
          placeholder="Cerca per cliente o note..."
          defaultValue={q}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Tutti gli stati</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento dei ritiri.</p>}
      {pickups && pickups.length === 0 && <p className="empty-state">Nessun ritiro trovato.</p>}

      {pickups && pickups.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Data</th>
              <th>Stato</th>
              <th>Tipo</th>
              <th>Animali</th>
              <th>Pratica</th>
            </tr>
          </thead>
          <tbody>
            {pickups.map((pickup) => (
              <tr key={pickup.id}>
                <td>
                  <Link to={`/ritiri/${pickup.id}`}>{new Date(pickup.start_at).toLocaleString("it-IT")}</Link>
                </td>
                <td>
                  <span className={`badge status-${pickup.pickup_status}`}>{STATUS_LABELS[pickup.pickup_status]}</span>
                </td>
                <td>{pickup.pickup_type}</td>
                <td>{pickup.animals.map((a) => a.name ?? "-").join(", ") || "-"}</td>
                <td>{pickup.linked_practice_id ? `#${pickup.linked_practice_id}` : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!pickups || pickups.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
