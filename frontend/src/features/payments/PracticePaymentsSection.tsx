import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  useClearTotalOverride,
  useMarkCollaboratorBilled,
  useSetTotalOverride,
  type Practice,
} from "@/features/practices/api";
import { useInvoicesForPractice, useLinkPaymentToInvoice } from "@/features/invoices/api";
import { useAuth } from "@/features/auth/useAuth";
import { euroStringToCents, formatMoney } from "@/shared/money";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";
import { Modal } from "@/shared/ui/Modal";

import { useDeletePayment, usePaymentsForPractice, usePracticeReconciliation, useRegisterPayment, useReversePayment } from "./api";
import type { PaymentCreateInput } from "./api";

const STATUS_LABELS: Record<string, string> = {
  non_pagata: "Non pagata",
  parziale: "Parzialmente pagata",
  pagata: "Pagata",
  sovrapagata: "Sovrapagata",
};

const COLLAB_LABELS: Record<string, string> = { da_fatturare: "Da fatturare", fatturato: "Fatturato" };

// V1 (registrazione pagamento): un vero popup (.payment-popover/
// .payment-dialog), non un form sempre visibile in fondo alla pagina.
// La logica di invio/idempotenza e' identica a prima, solo il
// contenitore visuale cambia (Modal condiviso).
function RegisterPaymentModal({ practiceId, open, onClose }: { practiceId: number; open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Registra pagamento">
      <RegisterPaymentForm practiceId={practiceId} onDone={onClose} />
    </Modal>
  );
}

function RegisterPaymentForm({ practiceId, onDone }: { practiceId: number; onDone: () => void }) {
  const registerPayment = useRegisterPayment();
  const [amountEuro, setAmountEuro] = useState("");
  const [channel, setChannel] = useState<PaymentCreateInput["channel"]>("W");
  const [movementType, setMovementType] = useState("Acconto");
  const [movementDate, setMovementDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null);
  // Stessa chiave per l'intero tentativo di invio: un doppio-click o un
  // retry di rete sulla STESSA richiesta arriva al backend con la stessa
  // idempotency_key e viene riconosciuto come lo stesso pagamento (mai un
  // secondo movimento attivo per lo stesso incasso - analisi Fase 10G sul
  // caso pratica 213). Rigenerata solo dopo un invio riuscito, cosi' il
  // pagamento successivo e' genuinamente un'operazione nuova.
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());

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
        idempotency_key: idempotencyKey,
      });
      setAmountEuro("");
      setIdempotencyKey(crypto.randomUUID());
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="field-row">
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
      </div>
      {error && <p className="field-error">{error}</p>}
      <div className="actions" style={{ marginTop: 16, justifyContent: "flex-end" }}>
        <button type="button" className="btn-ghost" onClick={onDone}>
          Annulla
        </button>
        <button className="btn" type="submit" disabled={registerPayment.isPending}>
          {registerPayment.isPending ? "Registrazione..." : "Registra pagamento"}
        </button>
      </div>
    </form>
  );
}

function LinkToInvoiceControl({ paymentId, invoices }: { paymentId: number; invoices: { id: number; invoice_number: string }[] }) {
  const linkPayment = useLinkPaymentToInvoice();
  // Continuità di navigazione: se l'utente sceglie una fattura a cui
  // collegare QUESTO pagamento, poi segue il link di un'altra fattura
  // nell'elenco sopra e torna con Indietro, la scelta deve essere ancora
  // presente - rappresentata nell'URL (chiave per paymentId, dato che
  // ogni pagamento ha il proprio controllo indipendente), non in uno
  // useState che uno smontaggio di route cancellerebbe.
  const [searchParams, setSearchParams] = useSearchParams();
  const paramKey = `collega_fattura_${paymentId}`;
  const selected = searchParams.get(paramKey) ?? "";
  function setSelected(value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(paramKey, value);
        else next.delete(paramKey);
        return next;
      },
      { replace: true },
    );
  }
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
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  // V1 (storno/eliminazione pagamento): un vero popup di conferma con
  // motivo obbligatorio (.payment-popover), non window.prompt nativo del
  // browser. Stesso identico comportamento/validazione di prima - solo il
  // contenitore visuale cambia (ConfirmModal condiviso). Il pagamento
  // target resta selezionato in stato React finche' il modal e' aperto.
  const [registerOpen, setRegisterOpen] = useState(false);
  const [reverseTargetId, setReverseTargetId] = useState<number | null>(null);
  const [deleteTargetId, setDeleteTargetId] = useState<number | null>(null);

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

  async function handleReverseConfirm(reason: string) {
    if (reverseTargetId == null) return;
    setActionError(null);
    try {
      await reversePayment.mutateAsync({ paymentId: reverseTargetId, reason });
      setReverseTargetId(null);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleDeleteConfirm(reason: string) {
    if (deleteTargetId == null) return;
    setActionError(null);
    try {
      await deletePayment.mutateAsync({ paymentId: deleteTargetId, deletionKind: "errore_inserimento", reason, practiceId: practice.id });
      setDeleteTargetId(null);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  return (
    <>
      {/* V1: economic_block/movement_invoice_section sono .section come
          tutti gli altri blocchi del dettaglio pratica (Riepilogo, Note,
          ...), non .card (quella e' riservata a card di form strette,
          max-width 520px - qui serve la larghezza piena della colonna). */}
      <div className="section">
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
            {isAdmin && (
              <button
                type="button"
                className="btn-ghost"
                style={{ marginLeft: 8 }}
                disabled={clearOverride.isPending}
                onClick={() => clearOverride.mutate(practice.id)}
              >
                Ripristina calcolo automatico
              </button>
            )}
          </p>
        ) : isAdmin ? (
          <form className="field-row" onSubmit={handleSetOverride}>
            <input inputMode="decimal" placeholder="Importo manuale €" value={overrideAmount} onChange={(e) => setOverrideAmount(e.target.value)} />
            <input placeholder="Motivo" value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} />
            <button className="btn-ghost" type="submit" disabled={setOverride.isPending}>
              Correggi totale manualmente
            </button>
          </form>
        ) : (
          <p className="sub">Solo gli amministratori possono correggere manualmente il totale di una pratica.</p>
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

      <div className="section">
        <h2>Fatture</h2>
        {(!invoices || invoices.length === 0) && <p className="empty-state">Nessuna fattura emessa per questa pratica.</p>}
        {invoices && invoices.length > 0 && (
          <ul className="reminders-todo-list">
            {invoices.map((invoice) => (
              <li key={invoice.id}>
                <Link className="reminders-todo-row" to={`/fatture/${invoice.id}`}>
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

      <div className="section">
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
                  <td>{new Date(`${payment.movement_date}T00:00:00`).toLocaleDateString("it-IT")}</td>
                  <td>{payment.movement_type}</td>
                  <td>{payment.channel}</td>
                  <td>{formatMoney(payment.amount_cents)}</td>
                  <td>
                    {payment.movement_type !== "Storno" && (
                      <>
                        <button className="btn-ghost" onClick={() => setReverseTargetId(payment.id)}>
                          Storna
                        </button>
                        {isAdmin && (
                          <button className="btn-ghost" onClick={() => setDeleteTargetId(payment.id)}>
                            Elimina
                          </button>
                        )}
                        <LinkToInvoiceControl paymentId={payment.id} invoices={invoices ?? []} />
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <button type="button" className="btn" onClick={() => setRegisterOpen(true)}>
          Registra pagamento
        </button>
      </div>

      <RegisterPaymentModal practiceId={practice.id} open={registerOpen} onClose={() => setRegisterOpen(false)} />

      <ConfirmModal
        open={reverseTargetId != null}
        onClose={() => setReverseTargetId(null)}
        onConfirm={handleReverseConfirm}
        title="Storna pagamento"
        description="Lo storno registra un movimento di compensazione (append-only): il pagamento originale resta nel ledger, non viene modificato ne' cancellato."
        requireReason
        reasonLabel="Motivo dello storno"
        confirmLabel="Storna"
        danger
        isPending={reversePayment.isPending}
      />

      <ConfirmModal
        open={deleteTargetId != null}
        onClose={() => setDeleteTargetId(null)}
        onConfirm={handleDeleteConfirm}
        title="Elimina pagamento"
        description="Il movimento resta nel ledger come eliminato (append-only) ed e' comunque ripristinabile."
        requireReason
        reasonLabel="Motivo della cancellazione"
        confirmLabel="Elimina"
        danger
        isPending={deletePayment.isPending}
      />
    </>
  );
}
