import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { formatMoney } from "@/shared/money";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import { useDeactivateUrn, useUrn, useUrnMovements } from "./api";

export function UrnDetailPage() {
  const { urnId } = useParams();
  const navigate = useNavigate();
  const id = Number(urnId);
  const { data: urn, isLoading, isError } = useUrn(id);
  const { data: movements } = useUrnMovements(id);
  const deactivate = useDeactivateUrn();
  const [deactivateModalOpen, setDeactivateModalOpen] = useState(false);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !urn) return <p className="error-banner">Articolo non trovato.</p>;

  // V1 (urn_catalog_page/urn_detail_page): stessa soglia low_stock_threshold
  // gia' usata dalla card in UrnListPage - stesso identico calcolo, non un
  // secondo criterio.
  const low = urn.quantity <= urn.low_stock_threshold;
  const stockCls = urn.quantity <= 0 ? "stock-out" : low ? "stock-low" : "stock-good";
  const stockLabel = urn.quantity <= 0 ? "Esaurita" : low ? "Scorta bassa" : "Disponibile";

  return (
    <main className="wrap">
      {/* V1 (urn_detail_page, app.py:13915): titlebar nome + sub
          materiale/codice, azioni Modifica/Catalogo nel titlebar stesso -
          poi <section class="grid urn-detail"> a due colonne (foto/
          placeholder + .kvs) invece della lista <p><strong> piatta di
          prima. Il badge Attivo/Disattivato non esiste in V1 (la pagina
          in V1 e' irraggiungibile per un articolo disattivato, 404) - qui
          resta perche' V2 permette di aprirla anche da "mostra anche gli
          articoli disattivati" nell'elenco, stato reale non fabbricato. */}
      <div className="titlebar">
        <div>
          <h1>{urn.name}</h1>
          <p className="sub">
            {urn.material || "Senza categoria"}
            {urn.internal_code ? ` · ${urn.internal_code}` : ""}
            {!urn.active && " · "}
            {!urn.active && <span className="badge status-inactive">Disattivato</span>}
          </p>
        </div>
        <div className="actions">
          <a className="btn" href={`/catalogo-urne/${urn.id}/modifica`} onClick={(e) => { e.preventDefault(); navigate(`/catalogo-urne/${urn.id}/modifica`); }}>
            Modifica
          </a>
          <a className="btn-ghost" href="/catalogo-urne" onClick={(e) => { e.preventDefault(); navigate("/catalogo-urne"); }}>
            Catalogo
          </a>
        </div>
      </div>

      <section className="grid urn-detail">
        <article className="section">
          {/* V1 mostra <img src={image_path}> quando presente - il backend
              V2 (schemas/urn.py) non espone image_path/upload per le urne,
              quindi qui compare sempre il placeholder reale. */}
          <div className="urn-placeholder">Nessuna foto</div>
        </article>
        <article className="section">
          <div className="kvs">
            <div className="kv">
              <small>Prezzo</small>
              <b>{formatMoney(urn.price_cents)}</b>
            </div>
            <div className="kv">
              <small>Quantita</small>
              <b>{urn.quantity}</b>
            </div>
            <div className="kv">
              <small>Stato</small>
              <b className={stockCls}>{stockLabel}</b>
            </div>
            <div className="kv">
              <small>Soglia scorte basse</small>
              <b>{urn.low_stock_threshold}</b>
            </div>
          </div>
          <h2 style={{ marginTop: 18 }}>Note</h2>
          <p>{urn.notes || <span className="sub">Nessuna nota.</span>}</p>
          {urn.active && (
            <button className="btn danger-btn" disabled={deactivate.isPending} onClick={() => setDeactivateModalOpen(true)}>
              Rimuovi dal catalogo
            </button>
          )}
        </article>
      </section>

      <section className="section" style={{ marginTop: 20 }}>
        <h2>Storico movimenti</h2>
        {(!movements || movements.length === 0) && <p className="sub">Nessun movimento registrato.</p>}
        {movements && movements.length > 0 && (
          <div className="timeline">
            {movements.map((m) => (
              <div className="event" key={m.id}>
                <b>{m.movement_type}</b> · {m.quantity_delta >= 0 ? "+" : ""}
                {m.quantity_delta} ({m.old_quantity} → {m.new_quantity})
                <br />
                {/* V1 (urn_detail_page, app.py:13913): valore grezzo di
                    created_at con "T" sostituita da uno spazio, nessuna
                    localizzazione it-IT (stesso pattern di
                    PracticeTrashPage) - stesso identico testo. */}
                <small className="sub">
                  {(m.created_at || "").replace("T", " ")}
                  {m.note ? ` · ${m.note}` : ""}
                </small>
              </div>
            ))}
          </div>
        )}
      </section>

      <ConfirmModal
        open={deactivateModalOpen}
        onClose={() => setDeactivateModalOpen(false)}
        onConfirm={() => {
          deactivate.mutate(urn.id);
          setDeactivateModalOpen(false);
        }}
        title="Rimuovi dal catalogo"
        description="Rimuovere questo articolo dal catalogo?"
        confirmLabel="Rimuovi"
        danger
        isPending={deactivate.isPending}
      />
    </main>
  );
}
