import { Link, useSearchParams } from "react-router-dom";

import { useListQueryParams } from "@/shared/useListQueryParams";

import { usePractices } from "./api";

const STATUS_LABELS: Record<string, string> = {
  ritirato: "Ritirato",
  in_programma: "In programma",
  cremato: "Cremato",
  da_consegnare: "Da consegnare",
  consegnato: "Consegnato",
  smaltito: "Smaltito",
};

export function PracticeListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  // Filtro aggiuntivo specifico di questa lista: stesso URLSearchParams
  // condiviso da useListQueryParams (react-router), non un secondo stato
  // parallelo - lo stato lista resta unico nell'URL.
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

  const { data: practices, isLoading, isError } = usePractices({ q, status: status || undefined, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Pratiche</h1>
        <Link className="btn" to="/pratiche/nuova">
          + Nuova pratica
        </Link>
      </div>

      <div className="field-row">
        <input
          className="search-input"
          placeholder="Cerca per numero pratica o cliente..."
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
      {isError && <p className="error-banner">Errore nel caricamento delle pratiche.</p>}
      {practices && practices.length === 0 && <p className="empty-state">Nessuna pratica trovata.</p>}

      {practices && practices.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Numero</th>
              <th>Stato</th>
              <th>Origine</th>
              <th>Servizio</th>
              <th>Animali</th>
            </tr>
          </thead>
          <tbody>
            {practices.map((practice) => (
              <tr key={practice.id}>
                <td>
                  <Link to={`/pratiche/${practice.id}`}>{practice.practice_number}</Link>
                </td>
                <td>
                  <span className={`badge status-${practice.status}`}>{STATUS_LABELS[practice.status]}</span>
                </td>
                <td>{practice.request_origin}</td>
                <td>{practice.service_type}</td>
                <td>{practice.animals.map((a) => a.name ?? "-").join(", ") || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!practices || practices.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
