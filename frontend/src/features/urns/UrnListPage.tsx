import { Link, useSearchParams } from "react-router-dom";

import { formatMoney } from "@/shared/money";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useUrns, type UrnCategoryValue } from "./api";

const CATEGORY_TABS: { value: UrnCategoryValue; label: string }[] = [
  { value: "Urna", label: "Urne" },
  { value: "Accessorio", label: "Accessori" },
  { value: "Calco", label: "Calchi" },
];

export function UrnListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const category = (searchParams.get("categoria") as UrnCategoryValue) || "Urna";
  const showInactive = searchParams.get("tutte") === "1";

  function setCategory(value: UrnCategoryValue) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("categoria", value);
      next.delete("offset");
      return next;
    });
  }

  const { data: urns, isLoading, isError } = useUrns({ category, activeOnly: !showInactive, q, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Catalogo Urne</h1>
        <Link className="btn" to={`/catalogo-urne/nuova?categoria=${category}`}>
          + Nuovo articolo
        </Link>
      </div>

      <nav className="calendar-tabs">
        {CATEGORY_TABS.map((tab) => (
          <a
            key={tab.value}
            className={tab.value === category ? "active" : ""}
            href="#"
            onClick={(e) => {
              e.preventDefault();
              setCategory(tab.value);
            }}
          >
            {tab.label}
          </a>
        ))}
      </nav>

      <input
        className="search-input"
        placeholder="Cerca per nome, codice o materiale..."
        defaultValue={q}
        onChange={(e) => setSearch(e.target.value)}
      />

      <label className="field-row">
        <input
          type="checkbox"
          checked={showInactive}
          onChange={(e) =>
            setSearchParams((prev) => {
              const next = new URLSearchParams(prev);
              if (e.target.checked) next.set("tutte", "1");
              else next.delete("tutte");
              next.delete("offset");
              return next;
            })
          }
        />
        Mostra anche gli articoli disattivati
      </label>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento del catalogo.</p>}
      {urns && urns.length === 0 && <p className="empty-state">Nessun articolo trovato.</p>}

      {urns && urns.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Codice</th>
              <th>Nome</th>
              <th>Materiale</th>
              <th>Prezzo</th>
              <th>Quantita</th>
              <th>Stato</th>
            </tr>
          </thead>
          <tbody>
            {urns.map((urn) => {
              const low = urn.quantity <= urn.low_stock_threshold;
              return (
                <tr key={urn.id}>
                  <td>
                    <Link to={`/catalogo-urne/${urn.id}`}>{urn.internal_code}</Link>
                  </td>
                  <td>{urn.name}</td>
                  <td>{urn.material || "-"}</td>
                  <td>{formatMoney(urn.price_cents)}</td>
                  <td>
                    <span className={`badge ${urn.quantity <= 0 ? "status-inactive" : low ? "status-pending" : "status-active"}`}>
                      {urn.quantity} pz
                    </span>
                  </td>
                  <td>{urn.active ? "Attivo" : "Disattivato"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!urns || urns.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
