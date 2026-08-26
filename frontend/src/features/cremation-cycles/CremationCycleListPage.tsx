import { Link, useSearchParams } from "react-router-dom";

import { useCremationCycles } from "./api";

const STATUS_LABELS: Record<string, string> = {
  pianificato: "Pianificato",
  in_attesa: "In attesa",
  completato: "Completato",
};

export function CremationCycleListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get("status") ?? "";
  const offset = Number(searchParams.get("offset") ?? "0");

  function setStatus(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set("status", value);
      else next.delete("status");
      next.delete("offset");
      return next;
    });
  }

  function setOffset(value: number) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("offset", String(value));
      return next;
    });
  }

  const { data: cycles, isLoading, isError } = useCremationCycles({ status: status || undefined, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Cicli di cremazione</h1>
        <Link className="btn" to="/cicli-cremazione/nuovo">
          + Nuovo ciclo
        </Link>
      </div>

      <div className="field-row">
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
      {isError && <p className="error-banner">Errore nel caricamento dei cicli.</p>}
      {cycles && cycles.length === 0 && <p className="empty-state">Nessun ciclo trovato.</p>}

      {cycles && cycles.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Data</th>
              <th>Orario previsto</th>
              <th>Stato</th>
              <th>Animali</th>
            </tr>
          </thead>
          <tbody>
            {cycles.map((cycle) => (
              <tr key={cycle.id}>
                <td>
                  <Link to={`/cicli-cremazione/${cycle.id}`}>{cycle.cycle_date}</Link>
                </td>
                <td>
                  {cycle.planned_start.slice(0, 5)} - {cycle.planned_end.slice(0, 5)}
                </td>
                <td>
                  <span className={`badge status-${cycle.status}`}>{STATUS_LABELS[cycle.status]}</span>
                </td>
                <td>{cycle.animals.length}/2</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!cycles || cycles.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
