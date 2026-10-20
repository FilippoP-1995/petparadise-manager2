import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";
import { CheckCircleIcon, ChevronRightIcon, XIcon } from "@/app/icons";
import { speciesAvatar } from "@/shared/speciesAvatar";
import { CREMATION_STATUS } from "@/shared/cremationStatus";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import {
  useAssignAnimal,
  useCompleteCycle,
  useCremationCycle,
  useDeleteCremationCycle,
  useEligibleAnimals,
  useRemoveAnimal,
  useRevertCycle,
} from "./api";

import "./cremationCycles.css";

export function CremationCycleDetailPage() {
  const { cycleId } = useParams();
  const navigate = useNavigate();
  const id = Number(cycleId);
  const { data: cycle, isLoading, isError } = useCremationCycle(id);
  const { data: eligibleAnimals } = useEligibleAnimals();
  const assignAnimal = useAssignAnimal();
  const removeAnimal = useRemoveAnimal();
  const completeCycle = useCompleteCycle();
  const revertCycle = useRevertCycle();
  const deleteCycle = useDeleteCremationCycle();
  const [actionError, setActionError] = useState<string | null>(null);
  const [completeModalOpen, setCompleteModalOpen] = useState(false);
  const [revertModalOpen, setRevertModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  // Continuità di navigazione: se l'utente seleziona un animale da
  // assegnare, poi segue il link verso la Pratica di un animale già
  // assegnato e torna con Indietro, la selezione deve essere ancora
  // presente - rappresentata nell'URL, non in uno useState che uno
  // smontaggio di route cancellerebbe.
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedAnimalId = Number(searchParams.get("animale")) || "";
  function setSelectedAnimalId(value: number | "") {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("animale", String(value));
        else next.delete("animale");
        return next;
      },
      { replace: true },
    );
  }
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !cycle) return <p className="error-banner">Ciclo non trovato.</p>;

  const isCompleted = cycle.status === "completato";
  const canAssign = !isCompleted && cycle.animals.length < 2;
  const canDelete = isAdmin && cycle.status === "pianificato" && cycle.animals.length === 0;

  async function handleAssign() {
    if (!selectedAnimalId) return;
    setActionError(null);
    try {
      await assignAnimal.mutateAsync({ cycleId: id, animalId: Number(selectedAnimalId) });
      setSelectedAnimalId("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleRemove(animalId: number) {
    setActionError(null);
    try {
      await removeAnimal.mutateAsync({ cycleId: id, animalId });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleComplete() {
    setActionError(null);
    try {
      await completeCycle.mutateAsync({ cycleId: id });
      setCompleteModalOpen(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleRevert(reason: string) {
    setActionError(null);
    try {
      await revertCycle.mutateAsync({ cycleId: id, reason });
      setRevertModalOpen(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleDelete() {
    setActionError(null);
    try {
      await deleteCycle.mutateAsync(id);
      navigate("/cicli-cremazione");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  return (
    // V1 (cremation_schedule, app.py:12225-12244): qui non esiste una pagina
    // di dettaglio dedicata - il ciclo si espande inline nella board
    // (cremationToggleCycleCard) mostrando .cremation-cycle-body con le
    // stesse righe animale reali (.cremation-animal-row, app.py:2261-2297).
    // V2 la trasforma in una pagina propria (stessa scelta gia' fatta per
    // la lista giornaliera) - qui portiamo pero' fedelmente il linguaggio
    // grafico reale delle righe animale (avatar, nome, azioni), non una
    // <table> generica come prima. Provenienza/etichette/urna non
    // renderizzate: CycleAnimalRead espone solo id/name/species/
    // practice_id, nessun dato in piu' da inventare (vedi anche
    // CremationCycleListPage.tsx).
    <main className="wrap">
      <div className="titlebar">
        <h1>Ciclo di cremazione {new Date(`${cycle.cycle_date}T00:00:00`).toLocaleDateString("it-IT")}</h1>
        <span className={`badge ${CREMATION_STATUS[cycle.status].cls}`}>{CREMATION_STATUS[cycle.status].label}</span>
      </div>

      <div className="section">
        <p>
          <strong>Orario previsto:</strong> <span className="cremation-cycle-time">{cycle.planned_start.slice(0, 5)} - {cycle.planned_end.slice(0, 5)}</span>
        </p>
        {cycle.completed_at && (
          <p>
            <strong>Completato il:</strong> {new Date(cycle.completed_at).toLocaleString("it-IT")}
          </p>
        )}
      </div>

      {actionError && <p className="error-banner">{actionError}</p>}

      <div className="section">
        <h2>Animali ({cycle.animals.length}/2)</h2>
        {cycle.animals.length === 0 && <p className="empty-state">Nessun animale assegnato.</p>}
        {cycle.animals.length > 0 && (
          <div className="cremation-detail-animals">
            {cycle.animals.map((animal) => {
              const [emoji, avatarCls] = speciesAvatar(animal.species);
              return (
                <div className="cremation-animal-row" key={animal.id}>
                  <div className="cremation-animal-id">
                    <span className={`cremation-animal-avatar ${avatarCls}`} aria-hidden="true">
                      {emoji}
                    </span>
                    <div className="cremation-animal-name-wrap">
                      <span className="cremation-animal-name">{animal.name ?? "Da inserire"}</span>
                      <span className="cremation-animal-weight">{animal.species ?? "-"}</span>
                    </div>
                  </div>
                  <div className="cremation-animal-actions">
                    {animal.practice_id && (
                      <Link className="cremation-animal-open" to={`/pratiche/${animal.practice_id}`}>
                        <span>Apri pratica</span>
                        <ChevronRightIcon />
                      </Link>
                    )}
                    {!isCompleted && (
                      <button
                        type="button"
                        className="cremation-animal-remove"
                        disabled={removeAnimal.isPending}
                        onClick={() => handleRemove(animal.id)}
                        aria-label="Rimuovi dal ciclo"
                        title="Rimuovi dal ciclo"
                      >
                        <XIcon />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {canAssign && (
          <div className="field-row">
            <select value={selectedAnimalId} onChange={(e) => setSelectedAnimalId(Number(e.target.value) || "")}>
              <option value="">Seleziona animale in attesa...</option>
              {eligibleAnimals?.map((animal) => (
                <option key={animal.id} value={animal.id}>
                  {animal.name ?? "(senza nome)"} - Pratica #{animal.practice_id}
                </option>
              ))}
            </select>
            <button className="btn" disabled={!selectedAnimalId || assignAnimal.isPending} onClick={handleAssign}>
              Assegna al ciclo
            </button>
          </div>
        )}
      </div>

      {/* V1 (app.py:12210/12221): stessi bottoni azione reali (classe +
          etichetta) gia' usati nella board giornaliera - "Termina ciclo" /
          "Annulla completamento", non testi V2 inventati. */}
      <div className="actions">
        {!isCompleted && cycle.animals.length > 0 && (
          <button
            type="button"
            className="cremation-action-btn cremation-action-active"
            disabled={completeCycle.isPending}
            onClick={() => setCompleteModalOpen(true)}
          >
            <CheckCircleIcon />
            <span>Termina ciclo</span>
          </button>
        )}
        {isCompleted && (
          <button
            type="button"
            className="cremation-action-btn cremation-action-planned"
            disabled={revertCycle.isPending}
            onClick={() => setRevertModalOpen(true)}
          >
            <span>Annulla completamento</span>
          </button>
        )}
        {canDelete && (
          <button className="btn-ghost" disabled={deleteCycle.isPending} onClick={() => setDeleteModalOpen(true)}>
            Elimina ciclo
          </button>
        )}
        <button className="btn-ghost" onClick={() => navigate("/cicli-cremazione")}>
          Torna all'elenco
        </button>
      </div>

      <ConfirmModal
        open={completeModalOpen}
        onClose={() => setCompleteModalOpen(false)}
        onConfirm={handleComplete}
        title="Completa ciclo"
        description='Confermi il completamento del ciclo? Le pratiche i cui animali sono tutti cremati passeranno a "cremato".'
        confirmLabel="Completa"
        isPending={completeCycle.isPending}
      />

      <ConfirmModal
        open={revertModalOpen}
        onClose={() => setRevertModalOpen(false)}
        onConfirm={handleRevert}
        title="Ripristina ciclo"
        requireReason
        reasonLabel="Motivo del ripristino"
        confirmLabel="Ripristina"
        danger
        isPending={revertCycle.isPending}
      />

      <ConfirmModal
        open={deleteModalOpen}
        onClose={() => setDeleteModalOpen(false)}
        onConfirm={handleDelete}
        title="Elimina ciclo"
        description="Confermi l'eliminazione del ciclo?"
        confirmLabel="Elimina"
        danger
        isPending={deleteCycle.isPending}
      />
    </main>
  );
}
