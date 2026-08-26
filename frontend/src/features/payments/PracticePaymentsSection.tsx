import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  useClearTotalOverride,
  useMarkCollaboratorBilled,
  useSetTotalOverride,
  type Practice,
} from "@/features/practices/api";
import { useInvoicesForPractice, useLinkPaymentToInvoice } from "@/features/invoices/api";
import { euroStringToCents, formatMoney } from "@/shared/money";

import { useDeletePayment, usePaymentsForPractice, usePracticeReconciliation, useRegisterPayment, useReversePayment } from "./api";
import type { PaymentCreateInput } from "./api";

const STATUS_LABELS: Record<string, string> = {
  non_pagata: "Non pagata",
  parziale: "Parzialmente pagata",
  pagata: "Pagata",
  sovrapagata: "Sovrapagata",
};

const COLLAB_LABELS: Record<string, string> = { da_fatturare: "Da fatturare", fatturato: "Fatturato" };

function RegisterPaymentForm({ practiceId, onDone }: { practiceId: number; onDone: () => void }) {
  const registerPayment = useRegisterPayment();
  const [amountEuro, setAmountEuro] = useState("");
  const [channel, setChannel] = useState<PaymentCreateInput["channel"]>("W");
  const [movementType, setMovementType] = useState("Acconto");
  const [movementDate, setMovementDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const amountCents = euroStringToCents(amountEuro);
    if (!amountCents) {
      setError("Importo non valido.");
      return;
    }
    setError(null);
    try {
      await registerPayment.mutateAsync({
        practice_id: practiceId,
        movement_date: movementDate,
        channel,
        ledger_section: "Entrata",
        movement_type: movementType,
        amount_cents: amountCents,
      });
      setAmountEuro("");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  return (
    <form className="field-row" onSubmit={handleSubmit}>
      <input inputMode="decimal" placeholder="Importo €" value={amountEuro} onChange={(e) => setAmountEuro(e.target.value)} />
      <select value={channel} onChange={(e) => setChannel(e.target.value as PaymentCreateInput["channel"])}>
        <option value="W">W</option>
        <option value="D">D</option>
        <option value="Collaboratori">Collaboratori</option>
      </select>
      <select value={movementType} onChange={(e) => setMovementType(e.target.value)}>
        <option value="Acconto">Acconto</option>
        <option value="Saldo">Saldo</option>
        <option value="Incasso completo">Incasso completo</option>
      </select>
      <input type="date" value={movementDate} onChange={(e) => setMovementDate(e.target.value)} />
      <button className="btn" type="submit" disabled={registerPayment.isPending}>
        Registra pagamento
      </button>
      {error && <p className="field-error">{error}</p>}
    </form>
  );
}

function LinkToInvoiceControl({ paymentId, invoices }: { paymentId: number; invoices: { id: number; invoice_number: string }[] }) {
  const linkPayment = useLinkPaymentToInvoice();
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (invoices.length === 0) return null;

  return (
    <span className="field-row" style={{ display: "inline-flex", gap: 4 }}>
      <select value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">Collega a fattura...</option>
        {invoices.map((invoice) => (
          <option key={invoice.id} value={invoice.id}>
            {invoice.invoice_number}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="btn-ghost"
        disabled={!selected || linkPayment.isPending}
        onClick={async () => {
          setError(null);
          try {
            await linkPayment.mutateAsync({ invoiceId: Number(selected), paymentId });
            setSelected("");
          } catch (err) {
            setError(err instanceof Error ? err.message : "Operazione non riuscita");
          }
        }}
      >
        Collega
      </button>
      {error && <span className="field-error">{error}</span>}
    </span>
  );
}

export function PracticePaymentsSection({ practice }: { practice: Practice }) {
  const { data: recon } = usePracticeReconciliation(practice.id);
  const { data: payments } = usePaymentsForPractice(practice.id);
  const { data: invoices } = useInvoicesForPractice(practice.id);
  const reversePayment = useReversePayment();
  const deletePayment = useDeletePayment();
  const setOverride = useSetTotalOverride();
  const clearOverride = useClearTotalOverride();
  const markCollaboratorBilled = useMarkCollaboratorBilled();
  const [overrideAmount, setOverrideAmount] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const hasOverride = practice.computed_total_override_cents != null;

  async function handleSetOverride(e: FormEvent) {
    e.preventDefault();
    const amountCents = euroStringToCents(overrideAmount);
    if (!amountCents || !overrideReason.trim()) {
      setActionError("Importo e motivo sono obbligatori.");
      return;
    }
    setActionError(null);
    try {
      await setOverride.mutateAsync({ practiceId: practice.id, amountCents, reason: overrideReason });
      setOverrideAmount("");
      setOverrideReason("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleReverse(paymentId: number) {
    const reason = window.prompt("Motivo dello storno (obbligatorio):");
    if (!reason) return;
    setActionError(null);
    try {
      await reversePayment.mutateAsync({ paymentId, reason });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleDelete(paymentId: number) {
    const reason = window.prompt("Motivo della cancellazione (obbligatorio - resta comunque ripristinabile):");
    if (!reason) return;
    setActionError(null);
    try {
      await deletePayment.mutateAsync({ paymentId, deletionKind: "errore_inserimento", reason, practiceId: practice.id });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  return (
    <>
      <div className="card">
        <h2>Riconciliazione</h2>
        <p className="sub">
          Totale effettivo (preventivo o importo corretto manualmente) e totale pagato restano sempre due cifre
          distinte.
        </p>
        {recon && (
          <>
            <div className="kvs">
              <div className="kv">
                <small>Totale effettivo</small>
                <b>{formatMoney(recon.effective_total_cents)}</b>
              </div>
              <div className="kv">
                <small>Pagato (W)</small>
                <b>{formatMoney(recon.paid_w_cents)}</b>
              </div>
              <div className="kv">
                <small>Pagato (D)</small>
                <b>{formatMoney(recon.paid_d_cents)}</b>
              </div>
              <div className="kv">
                <small>Pagato (Collaboratori)</small>
                <b>{formatMoney(recon.paid_collaboratori_cents)}</b>
              </div>
              <div className="kv">
                <small>Residuo</small>
                <b>{formatMoney(recon.residual_cents)}</b>
              </div>
              <div className="kv">
                <small>Stato</small>
                <b className={`badge status-${recon.status}`}>{STATUS_LABELS[recon.status]}</b>
              </div>
            </div>
            {recon.status === "sovrapagata" && (
              <p className="flash warning">
                Sovrapagamento: {formatMoney(recon.paid_total_cents - recon.effective_total_cents)} incassati in piu' del
                dovuto. Non corretto automaticamente - valuta uno storno.
              </p>
            )}
          </>
        )}

        {hasOverride ? (
          <p className="flash warning">
            Totale corretto manualmente a {formatMoney(practice.computed_total_override_cents ?? 0)}
            {practice.computed_total_override_reason ? ` - ${practice.computed_total_override_reason}` : ""}. Il
            totale preventivo ({formatMoney(practice.line_items_total_cents)}) resta calcolato a fianco, mai nascosto.
            <button
              type="button"
              className="btn-ghost"
              style={{ marginLeft: 8 }}
              disabled={clearOverride.isPending}
              onClick={() => clearOverride.mutate(practice.id)}
            >
              Ripristina calcolo automatico
            </button>
          </p>
        ) : (
          <form className="field-row" onSubmit={handleSetOverride}>
            <input inputMode="decimal" placeholder="Importo manuale €" value={overrideAmount} onChange={(e) => setOverrideAmount(e.target.value)} />
            <input placeholder="Motivo" value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} />
            <button className="btn-ghost" type="submit" disabled={setOverride.isPending}>
              Correggi totale manualmente
            </button>
          </form>
        )}

        {practice.collaborator_id != null && (
          <p className="sub" style={{ marginTop: 12 }}>
            Fatturazione collaboratore: <b>{COLLAB_LABELS[practice.collaborator_billing_status]}</b>
            {practice.collaborator_billing_status === "da_fatturare" && (
              <button
                type="button"
                className="btn-ghost"
                style={{ marginLeft: 8 }}
                disabled={markCollaboratorBilled.isPending}
                onClick={() => markCollaboratorBilled.mutate(practice.id)}
              >
                Segna come fatturato
              </button>
            )}
          </p>
        )}
      </div>

      {actionError && <p className="error-banner">{actionError}</p>}

      <div className="card">
        <h2>Fatture</h2>
        {(!invoices || invoices.length === 0) && <p className="empty-state">Nessuna fattura emessa per questa pratica.</p>}
        {invoices && invoices.length > 0 && (
          <ul className="reminders-todo-list">
            {invoices.map((invoice) => (
              <li key={invoice.id}>
                <Link to={`/fatture/${invoice.id}`}>
                  {invoice.invoice_number} - {formatMoney(invoice.total_amount_cents)} ({invoice.channel})
                </Link>
              </li>
            ))}
          </ul>
        )}
        <Link className="btn-ghost" to={`/fatture/nuova?practice_id=${practice.id}`}>
          + Nuova fattura
        </Link>
      </div>

      <div className="card">
        <h2>Pagamenti</h2>
        {(!payments || payments.length === 0) && <p className="empty-state">Nessun pagamento registrato.</p>}
        {payments && payments.length > 0 && (
          <table className="data-table">
            <thead>
              <tr>
                <th>Data</th>
                <th>Tipo</th>
                <th>Canale</th>
                <th>Importo</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {payments.map((payment) => (
                <tr key={payment.id}>
                  <td>{payment.movement_date}</td>
                  <td>{payment.movement_type}</td>
                  <td>{payment.channel}</td>
                  <td>{formatMoney(payment.amount_cents)}</td>
                  <td>
                    {payment.movement_type !== "Storno" && (
                      <>
                        <button className="btn-ghost" onClick={() => handleReverse(payment.id)}>
                          Storna
                        </button>
                        <button className="btn-ghost" onClick={() => handleDelete(payment.id)}>
                          Elimina
                        </button>
                        <LinkToInvoiceControl paymentId={payment.id} invoices={invoices ?? []} />
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <RegisterPaymentForm practiceId={practice.id} onDone={() => {}} />
      </div>
    </>
  );
}
