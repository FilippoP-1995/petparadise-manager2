import { Link, useNavigate, useParams } from "react-router-dom";

import { useInvoice, useInvoiceReconciliation } from "./api";

function money(cents: number) {
  return (cents / 100).toLocaleString("it-IT", { style: "currency", currency: "EUR" });
}

const STATUS_LABELS: Record<string, string> = {
  non_pagata: "Non pagata",
  parziale: "Parzialmente pagata",
  pagata: "Pagata",
  sovrapagata: "Sovrapagata",
};

export function InvoiceDetailPage() {
  const { invoiceId } = useParams();
  const navigate = useNavigate();
  const id = Number(invoiceId);
  const { data: invoice, isLoading, isError } = useInvoice(id);
  const { data: recon } = useInvoiceReconciliation(id);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !invoice) return <p className="error-banner">Fattura non trovata.</p>;

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>{invoice.invoice_number}</h1>
        {recon && <span className={`badge status-${recon.status}`}>{STATUS_LABELS[recon.status]}</span>}
      </div>

      <div className="card">
        <p>
          <strong>Pratica:</strong>{" "}
          {invoice.practice_id ? <Link to={`/pratiche/${invoice.practice_id}`}>{invoice.practice_number_snapshot}</Link> : invoice.practice_number_snapshot || "-"}
        </p>
        <p>
          <strong>Data fattura:</strong> {invoice.invoice_date ?? "-"}
        </p>
        <p>
          <strong>Canale:</strong> {invoice.channel}
        </p>
      </div>

      {recon && (
        <div className="card">
          <h2>Riconciliazione</h2>
          <p className="sub">
            Il totale della fattura (documento fiscale) e il totale pagato (ledger) sono due cifre distinte - non
            collassano mai l'una sull'altra.
          </p>
          <div className="kvs">
            <div className="kv">
              <small>Totale fattura</small>
              <b>{money(recon.total_amount_cents)}</b>
            </div>
            <div className="kv">
              <small>Totale pagato</small>
              <b>{money(recon.paid_cents)}</b>
            </div>
            <div className="kv">
              <small>Residuo</small>
              <b>{money(recon.residual_cents)}</b>
            </div>
          </div>
          {recon.status === "sovrapagata" && (
            <p className="flash warning">
              Sovrapagamento rilevato: sono stati incassati {money(recon.paid_cents - recon.total_amount_cents)} in
              piu' rispetto al documento fiscale. Non viene corretto automaticamente - verifica se serve uno storno o
              una correzione della fattura.
            </p>
          )}
        </div>
      )}

      <div className="actions">
        <button className="btn-ghost" onClick={() => navigate("/fatture")}>
          Torna all'elenco
        </button>
      </div>
    </main>
  );
}
