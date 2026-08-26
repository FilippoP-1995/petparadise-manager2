import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useCompanyLocations } from "@/features/practices/api";

import {
  useCancelPickup,
  useCancelPickupAndTrashPractice,
  useCreatePracticeFromPickup,
  usePickup,
  useTransitionPickup,
  type PickupStatusValue,
} from "./api";

const STATUS_LABELS: Record<string, string> = {
  da_confermare: "Da confermare",
  da_ritirare: "Da ritirare",
  ritirato: "Ritirato",
  annullato: "Annullato",
};

// Stesso grafo di domain/pickup/state_machine.py, solo per decidere quali
// pulsanti mostrare - la validazione reale resta sempre lato backend.
const ALLOWED_NEXT: Record<string, PickupStatusValue[]> = {
  da_confermare: ["da_ritirare", "annullato"],
  da_ritirare: ["ritirato", "annullato"],
  ritirato: ["annullato"],
  annullato: [],
};

export function PickupDetailPage() {
  const { pickupId } = useParams();
  const navigate = useNavigate();
  const id = Number(pickupId);
  const { data: pickup, isLoading, isError } = usePickup(id);
  const transition = useTransitionPickup();
  const cancel = useCancelPickup();
  const cancelAndTrash = useCancelPickupAndTrashPractice();
  const createPractice = useCreatePracticeFromPickup();
  const { data: locations } = useCompanyLocations();
  const [actionError, setActionError] = useState<string | null>(null);
  const [showCreatePracticeForm, setShowCreatePracticeForm] = useState(false);
  const [destinationBranchId, setDestinationBranchId] = useState<number | "">("");

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !pickup) return <p className="error-banner">Ritiro non trovato.</p>;

  async function handleTransition(target: PickupStatusValue) {
    setActionError(null);
    try {
      await transition.mutateAsync({ pickupId: id, targetStatus: target });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleCancel() {
    const reason = window.prompt("Motivo dell'annullamento (opzionale):") ?? undefined;
    setActionError(null);
    try {
      await cancel.mutateAsync({ pickupId: id, reason });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleCancelAndTrashPractice() {
    if (
      !window.confirm(
        `Verra' cestinata anche la pratica #${pickup!.linked_practice_id} collegata - confermi?`,
      )
    )
      return;
    const reason = window.prompt("Motivo (obbligatorio):");
    if (!reason) return;
    setActionError(null);
    try {
      await cancelAndTrash.mutateAsync({ pickupId: id, reason });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleCreatePractice() {
    if (!destinationBranchId) return;
    setActionError(null);
    try {
      const practice = await createPractice.mutateAsync({
        pickupId: id,
        destinationBranchId: Number(destinationBranchId),
        serviceType: "Da decidere",
      });
      navigate(`/pratiche/${practice.id}`);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  const nextStates = ALLOWED_NEXT[pickup.pickup_status].filter((s) => s !== "annullato");
  const canCreatePractice = pickup.pickup_status === "ritirato" && !pickup.linked_practice_id;

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Ritiro {new Date(pickup.start_at).toLocaleString("it-IT")}</h1>
        <span className={`badge status-${pickup.pickup_status}`}>{STATUS_LABELS[pickup.pickup_status]}</span>
      </div>

      <div className="card">
        <p>
          <strong>Tipo:</strong> {pickup.pickup_type}
        </p>
        <p>
          <strong>Animali:</strong> {pickup.animals.map((a) => a.name ?? "-").join(", ") || "Nessuno"}
        </p>
        {pickup.linked_practice_id && (
          <p className="flash warning">
            Collegato alla pratica #{pickup.linked_practice_id}
            {pickup.pickup_status === "annullato" && " - il ritiro e' annullato ma la pratica NON e' stata modificata."}
          </p>
        )}
      </div>

      {actionError && <p className="error-banner">{actionError}</p>}

      {canCreatePractice && (
        <div className="card">
          <h2>Crea pratica da questo ritiro</h2>
          {showCreatePracticeForm ? (
            <div className="field-row">
              <select value={destinationBranchId} onChange={(e) => setDestinationBranchId(Number(e.target.value) || "")}>
                <option value="">Seleziona sede...</option>
                {locations?.map((loc) => (
                  <option key={loc.id} value={loc.id}>
                    {loc.name}
                  </option>
                ))}
              </select>
              <button className="btn" disabled={!destinationBranchId || createPractice.isPending} onClick={handleCreatePractice}>
                Conferma creazione
              </button>
            </div>
          ) : (
            <button className="btn" onClick={() => setShowCreatePracticeForm(true)}>
              Crea pratica
            </button>
          )}
        </div>
      )}

      <div className="actions">
        {nextStates.map((target) => (
          <button key={target} className="btn" disabled={transition.isPending} onClick={() => handleTransition(target)}>
            Porta a &quot;{STATUS_LABELS[target]}&quot;
          </button>
        ))}
        {pickup.pickup_status !== "annullato" && (
          <button className="btn-ghost" disabled={cancel.isPending} onClick={handleCancel}>
            Annulla ritiro
          </button>
        )}
        {pickup.linked_practice_id && pickup.pickup_status !== "annullato" && (
          <button className="btn-ghost" disabled={cancelAndTrash.isPending} onClick={handleCancelAndTrashPractice}>
            Annulla ritiro e cestina anche la pratica
          </button>
        )}
        <button className="btn-ghost" onClick={() => navigate("/ritiri")}>
          Torna all'elenco
        </button>
      </div>
    </main>
  );
}
