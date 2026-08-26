import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { usePractice, useTransitionPractice, useTrashPractice, type PracticeStatusValue } from "./api";

const WORKFLOW_NEXT: Record<string, PracticeStatusValue> = {
  ritirato: "in_programma",
  in_programma: "cremato",
  cremato: "da_consegnare",
  da_consegnare: "consegnato",
  consegnato: "smaltito",
};

const STATUS_LABELS: Record<string, string> = {
  ritirato: "Ritirato",
  in_programma: "In programma",
  cremato: "Cremato",
  da_consegnare: "Da consegnare",
  consegnato: "Consegnato",
  smaltito: "Smaltito",
};

export function PracticeDetailPage() {
  const { practiceId } = useParams();
  const navigate = useNavigate();
  const id = Number(practiceId);
  const { data: practice, isLoading, isError } = usePractice(id);
  const transition = useTransitionPractice();
  const trash = useTrashPractice();
  const [actionError, setActionError] = useState<string | null>(null);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !practice) return <p className="error-banner">Pratica non trovata.</p>;

  // Copia narrowed: TS non propaga il narrowing di `practice` dentro le
  // closure annidate sotto (handleAdvance/handleTrash).
  const currentPractice = practice;
  const nextStatus = WORKFLOW_NEXT[currentPractice.status];

  async function handleAdvance() {
    if (!nextStatus) return;
    setActionError(null);
    try {
      await transition.mutateAsync({ practiceId: id, targetStatus: nextStatus });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleTrash() {
    if (!confirm(`Cestinare la pratica ${currentPractice.practice_number}?`)) return;
    await trash.mutateAsync(id);
    navigate("/pratiche");
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>{practice.practice_number}</h1>
        <span className={`badge status-${practice.status}`}>{STATUS_LABELS[practice.status]}</span>
      </div>

      <div className="card">
        <p>
          <strong>Origine:</strong> {practice.request_origin}
        </p>
        <p>
          <strong>Servizio:</strong> {practice.service_type}
        </p>
        <p>
          <strong>Animali:</strong> {practice.animals.map((a) => a.name ?? "-").join(", ") || "Nessuno"}
        </p>
        <p>
          <strong>Totale preventivo:</strong> {(practice.line_items_total_cents / 100).toFixed(2)} €
        </p>
        {(practice.tags ?? []).length > 0 && (
          <p>
            <strong>Tag:</strong> {(practice.tags ?? []).join(", ")}
          </p>
        )}
      </div>

      {actionError && <p className="error-banner">{actionError}</p>}

      <div className="actions">
        {nextStatus && (
          <button className="btn" disabled={transition.isPending} onClick={handleAdvance}>
            {transition.isPending ? "Aggiornamento..." : `Porta a "${STATUS_LABELS[nextStatus]}"`}
          </button>
        )}
        <button className="btn-ghost" onClick={handleTrash}>
          Cestina
        </button>
        <button className="btn-ghost" onClick={() => navigate("/pratiche")}>
          Torna all'elenco
        </button>
      </div>
    </main>
  );
}
