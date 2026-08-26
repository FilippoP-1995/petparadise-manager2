import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";
import { euroStringToCents, formatMoney } from "@/shared/money";

import { useCorrectInvoiceTotal, useInvoice, useInvoiceReconciliation } from "./api";

const STATUS_LABELS: Record<string, string> = {
  non_pagata: "Non pagata",
  parziale: "Parzialmente pagata",
  pagata: "Pagata",
  sovrapagata: "Sovrapagata",
};

function CorrectInvoiceTotalForm({ invoiceId, currentTotalCents }: { invoiceId: number; currentTotalCents: number }) {
  const correctTotal = useCorrectInvoiceTotal();
  const [open, setOpen] = useState(false);
  const [amountEuro, setAmountEuro] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <button type="button" className="btn-ghost" onClick={() => setOpen(true)}>
        Correggi totale fattura
      </button>
    );
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const amountCents = euroStringToCents(amountEuro);
    if (!amountCents || amountCents <= 0) {
      setError("Importo non valido.");
      return;
    }
    if (!reason.trim()) {
      setError("Il motivo e' obbligatorio.");
      return;
    }
    setError(null);
    try {
      await correctTotal.mutateAsync({ invoiceId, totalAmountCents: amountCents, reason });
      setOpen(false);
      setAmountEuro("");
      setReason("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  return (
    <form className="field-row" onSubmit={handleSubmit}>
      <input
        inputMode="decimal"
        placeholder={`Attuale: ${formatMoney(currentTotalCents)}`}
        value={amountEuro}
        onChange={(e) => setAmountEuro(e.target.value)}
      />
      <input placeholder="Motivo della correzione" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button className="btn" type="submit" disabled={correctTotal.isPending}>
        Conferma correzione
      </button>
      <button type="button" className="btn-ghost" onClick={() => setOpen(false)}>
        Annulla
      </button>
      {error && <p className="field-error">{error}</p>}
    </form>
  );
}

export function InvoiceDetailPage() {
  const { invoiceId } = useParams();
  const navigate = useNavigate();
  const id = Number(invoiceId);
  const { data: invoice, isLoading, isError } = useInvoice(id);
  const { data: recon } = useInvoiceReconciliation(id);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

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
              <b>{formatMoney(recon.total_amount_cents)}</b>
            </div>
            <div className="kv">
              <small>Totale pagato</small>
              <b>{formatMoney(recon.paid_cents)}</b>
            </div>
            <div className="kv">
              <small>Residuo</small>
              <b>{formatMoney(recon.residual_cents)}</b>
            </div>
          </div>
          {recon.status === "sovrapagata" && (
            <p className="flash warning">
              Sovrapagamento rilevato: sono stati incassati {formatMoney(recon.paid_cents - recon.total_amount_cents)} in
              piu' rispetto al documento fiscale. Non viene corretto automaticamente - verifica se serve uno storno o
              una correzione della fattura.
            </p>
          )}

          {isAdmin ? (
            <div style={{ marginTop: 12 }}>
              <p className="sub">
                Correzione eccezionale del documento fiscale - solo Admin, motivo obbligatorio, tracciata in
                audit_log. I pagamenti gia' collegati non vengono mai toccati.
              </p>
              <CorrectInvoiceTotalForm invoiceId={invoice.id} currentTotalCents={invoice.total_amount_cents} />
            </div>
          ) : (
            <p className="sub" style={{ marginTop: 12 }}>
              Solo gli amministratori possono correggere il totale di una fattura emessa.
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
