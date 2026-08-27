import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";

import {
  useAssignAnimal,
  useCompleteCycle,
  useCremationCycle,
  useDeleteCremationCycle,
  useEligibleAnimals,
  useRemoveAnimal,
  useRevertCycle,
} from "./api";

const STATUS_LABELS: Record<string, string> = {
  pianificato: "Pianificato",
  in_attesa: "In attesa",
  completato: "Completato",
};

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
    if (!window.confirm("Confermi il completamento del ciclo? Le pratiche i cui animali sono tutti cremati passeranno a \"cremato\".")) return;
    setActionError(null);
    try {
      await completeCycle.mutateAsync({ cycleId: id });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleRevert() {
    const reason = window.prompt("Motivo del ripristino (obbligatorio):");
    if (!reason) return;
    setActionError(null);
    try {
      await revertCycle.mutateAsync({ cycleId: id, reason });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleDelete() {
    if (!window.confirm("Confermi l'eliminazione del ciclo?")) return;
    setActionError(null);
    try {
      await deleteCycle.mutateAsync(id);
      navigate("/cicli-cremazione");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Ciclo di cremazione {cycle.cycle_date}</h1>
        <span className={`badge status-${cycle.status}`}>{STATUS_LABELS[cycle.status]}</span>
      </div>

      <div className="card">
        <p>
          <strong>Orario previsto:</strong> {cycle.planned_start.slice(0, 5)} - {cycle.planned_end.slice(0, 5)}
        </p>
        {cycle.completed_at && (
          <p>
            <strong>Completato il:</strong> {new Date(cycle.completed_at).toLocaleString("it-IT")}
          </p>
        )}
      </div>

      {actionError && <p className="error-banner">{actionError}</p>}

      <div className="card">
        <h2>Animali ({cycle.animals.length}/2)</h2>
        {cycle.animals.length === 0 && <p className="empty-state">Nessun animale assegnato.</p>}
        {cycle.animals.length > 0 && (
          <table className="data-table">
            <thead>
              <tr>
                <th>Nome</th>
                <th>Specie</th>
                <th>Pratica</th>
                {!isCompleted && <th></th>}
              </tr>
            </thead>
            <tbody>
              {cycle.animals.map((animal) => (
                <tr key={animal.id}>
                  <td>{animal.name ?? "-"}</td>
                  <td>{animal.species ?? "-"}</td>
                  <td>{animal.practice_id ? <Link to={`/pratiche/${animal.practice_id}`}>#{animal.practice_id}</Link> : "-"}</td>
                  {!isCompleted && (
                    <td>
                      <button className="btn-ghost" disabled={removeAnimal.isPending} onClick={() => handleRemove(animal.id)}>
                        Rimuovi
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
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

      <div className="actions">
        {!isCompleted && cycle.animals.length > 0 && (
          <button className="btn" disabled={completeCycle.isPending} onClick={handleComplete}>
            Completa ciclo
          </button>
        )}
        {isCompleted && (
          <button className="btn-ghost" disabled={revertCycle.isPending} onClick={handleRevert}>
            Ripristina (correzione)
          </button>
        )}
        {canDelete && (
          <button className="btn-ghost" disabled={deleteCycle.isPending} onClick={handleDelete}>
            Elimina ciclo
          </button>
        )}
        <button className="btn-ghost" onClick={() => navigate("/cicli-cremazione")}>
          Torna all'elenco
        </button>
      </div>
    </main>
  );
}
