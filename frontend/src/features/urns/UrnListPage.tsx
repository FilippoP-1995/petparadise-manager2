import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { formatMoney } from "@/shared/money";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useUrnCategoryStats, useUrns, type UrnCategoryValue } from "./api";

const CATEGORY_TABS: { value: UrnCategoryValue; label: string }[] = [
  { value: "Urna", label: "Urne" },
  { value: "Accessorio", label: "Accessori" },
  { value: "Calco", label: "Calchi" },
];

// V1 (urn_catalog_page, app.py:13857/13874): il testo del bottone dipende
// dalla categoria attiva, non un generico "+ Nuovo articolo" fisso.
const NEW_ITEM_LABEL: Record<UrnCategoryValue, string> = {
  Urna: "+ Nuova urna",
  Accessorio: "+ Nuovo accessorio",
  Calco: "+ Nuovo calco",
};

// V1 (urn_catalog_page, app.py:13871): opzioni fisse del filtro
// Disponibilita, stesso value/label esatti usati per le query string.
const AVAILABILITY_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Tutte le disponibilita" },
  { value: "disponibile", label: "Disponibili" },
  { value: "bassa", label: "Scorte basse" },
  { value: "esaurita", label: "Esaurite" },
];

export function UrnListPage() {
  const { q, setSearch } = useListQueryParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const category = (searchParams.get("categoria") as UrnCategoryValue) || "Urna";
  const showInactive = searchParams.get("tutte") === "1";
  const materialFilter = searchParams.get("materiale") ?? "";
  const availabilityFilter = searchParams.get("disponibilita") ?? "";

  function setCategory(value: UrnCategoryValue) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("categoria", value);
        // V1: i tab sono semplici <a href="/catalogo-urne?categoria=..."> -
        // cambiare categoria riparte sempre da un filtro pulito.
        next.delete("materiale");
        next.delete("disponibilita");
        next.delete("offset");
        return next;
      },
      { replace: true },
    );
  }

  function setFilterParam(name: string, value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(name, value);
        else next.delete(name);
        return next;
      },
      { replace: true },
    );
  }

  // V1 (urn_catalog_page, app.py:13853): nessun LIMIT/OFFSET nella query -
  // la pagina mostra sempre l'intero risultato filtrato, senza paginazione.
  // Qui si usa il limite massimo consentito dall'API (query "limit" fino a
  // 200, app/api/routes/urns.py) al posto di una vera lista illimitata,
  // dato che il backend non espone un "mostra tutto" senza cap.
  const { data: urns, isLoading, isError } = useUrns({ category, activeOnly: !showInactive, q, limit: 200 });
  const { data: stats } = useUrnCategoryStats(category);

  // V1 (urn_catalog_page, app.py:13848/13850): Materiale e Disponibilita
  // sono filtri SQL lato server (query string materiale/disponibilita).
  // L'API V2 (GET /api/urns) non accetta questi parametri e non espone un
  // endpoint per i materiali distinti: qui si filtra lato client sui dati
  // gia' caricati per la categoria corrente, usando gli stessi identici
  // campi reali (material/quantity/low_stock_threshold) invece di
  // fabbricare un filtro finto. La lista Materiale riflette quindi solo le
  // urne effettivamente caricate (fino al limite di 200), non l'intera
  // categoria come in V1.
  const materials = useMemo(() => {
    if (!urns) return [];
    return Array.from(new Set(urns.map((u) => u.material).filter((m): m is string => !!m))).sort((a, b) =>
      a.localeCompare(b, "it"),
    );
  }, [urns]);

  const filteredUrns = useMemo(() => {
    if (!urns) return undefined;
    return urns.filter((urn) => {
      if (materialFilter && urn.material !== materialFilter) return false;
      if (availabilityFilter) {
        const low = urn.quantity <= urn.low_stock_threshold;
        const status = urn.quantity <= 0 ? "esaurita" : low ? "bassa" : "disponibile";
        if (status !== availabilityFilter) return false;
      }
      return true;
    });
  }, [urns, materialFilter, availabilityFilter]);

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Catalogo Urne</h1>
          <p className="sub">Catalogo e magazzino collegati alle pratiche.</p>
        </div>
        <Link className="btn" to={`/catalogo-urne/nuova?categoria=${category}`}>
          {NEW_ITEM_LABEL[category]}
        </Link>
      </div>

      <nav className="calendar-tabs urn-tabs">
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

      {stats && (
        <div className="urn-stats">
          <div className="urn-stat">
            <small>Modelli</small>
            <strong>{stats.models}</strong>
          </div>
          <div className="urn-stat">
            <small>Pezzi disponibili</small>
            <strong>{stats.quantity}</strong>
          </div>
          <div className="urn-stat">
            <small>Esaurite</small>
            <strong>{stats.out}</strong>
          </div>
          <div className="urn-stat">
            <small>Scorte basse</small>
            <strong>{stats.low}</strong>
          </div>
          <div className="urn-stat">
            <small>Valore magazzino</small>
            <strong>{formatMoney(stats.value_cents)}</strong>
          </div>
        </div>
      )}

      {/* V1 (urn_catalog_page, app.py:13874): <form class="section urn-filter
          no-advanced-collapse"> con campo ricerca + Materiale + Disponibilita
          + bottone "Filtra" - non un singolo <input class="search-input">
          isolato (classe mai usata da questa pagina in V1). La ricerca resta
          applicata in tempo reale (query "q" gia' server-side), Materiale e
          Disponibilita filtrano invece lato client (vedi sopra) appena
          selezionati - il submit del form non deve quindi ricaricare nulla. */}
      <form className="section urn-filter no-advanced-collapse" onSubmit={(e) => e.preventDefault()}>
        <div className="fields">
          <div className="field full">
            <label>
              Cerca urna
              <input
                id="urnCatalogSearch"
                placeholder="Inizia a scrivere il nome dell'urna"
                autoComplete="off"
                defaultValue={q}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
          </div>
          <div className="field">
            <label>
              Materiale
              <select value={materialFilter} onChange={(e) => setFilterParam("materiale", e.target.value)}>
                <option value="">Tutti i materiali</option>
                {materials.map((material) => (
                  <option key={material} value={material}>
                    {material}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="field">
            <label>
              Disponibilita
              <select value={availabilityFilter} onChange={(e) => setFilterParam("disponibilita", e.target.value)}>
                {AVAILABILITY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
        <button className="btn" style={{ marginTop: 12 }}>
          Filtra
        </button>
      </form>

      <label className="field-row">
        <input
          type="checkbox"
          checked={showInactive}
          onChange={(e) =>
            setSearchParams(
              (prev) => {
                const next = new URLSearchParams(prev);
                if (e.target.checked) next.set("tutte", "1");
                else next.delete("tutte");
                next.delete("offset");
                return next;
              },
              { replace: true },
            )
          }
        />
        Mostra anche gli articoli disattivati
      </label>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento del catalogo.</p>}
      {/* V1 (urn_catalog_page, app.py:13872-13873): due messaggi distinti -
          nessuna riga dal server ("Puoi aggiungerne una nuova") contro
          risultato azzerato dal solo filtro aggiuntivo lato client
          ("...alla ricerca"), stesso testo esatto in entrambi i casi. */}
      {urns && urns.length === 0 && (
        <section className="section empty-state">Nessuna urna corrisponde ai filtri. Puoi aggiungerne una nuova.</section>
      )}
      {urns && urns.length > 0 && filteredUrns && filteredUrns.length === 0 && (
        <section className="section empty-state">Nessuna urna corrisponde alla ricerca.</section>
      )}

      {filteredUrns && filteredUrns.length > 0 && (
        <section className="urn-grid">
          {filteredUrns.map((urn) => {
            const low = urn.quantity <= urn.low_stock_threshold;
            const stockCls = urn.quantity <= 0 ? "stock-out" : low ? "stock-low" : "stock-good";
            const stockLabel = urn.quantity <= 0 ? "Esaurita" : low ? "Scorta bassa" : "Disponibile";
            return (
              <article className="urn-card" key={urn.id}>
                {/* V1 mostra <img src={image_path}> quando presente - il
                    backend V2 (schemas/urn.py) non espone ancora
                    image_path/upload per le urne, quindi qui compare sempre
                    il placeholder reale invece di una foto finta. */}
                <div className="urn-placeholder">Nessuna foto</div>
                <div>
                  <span className={stockCls}>
                    <b>{stockLabel}</b> · {urn.quantity} pz
                  </span>
                  <h2>{urn.name}</h2>
                  <div className="urn-meta">
                    {urn.material || "Senza categoria"}
                    {urn.internal_code ? ` · ${urn.internal_code}` : ""}
                  </div>
                  <strong>{formatMoney(urn.price_cents)}</strong>
                  {!urn.active && (
                    <div>
                      <span className="badge status-inactive">Disattivato</span>
                    </div>
                  )}
                </div>
                <div className="actions">
                  <Link className="btn" to={`/catalogo-urne/${urn.id}`}>
                    Apri scheda
                  </Link>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}
