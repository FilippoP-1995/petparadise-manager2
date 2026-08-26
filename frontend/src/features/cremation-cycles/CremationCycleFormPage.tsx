import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { useCompanyLocations } from "@/features/practices/api";

import { useCreateCremationCycle } from "./api";

const schema = z.object({
  cycle_date: z.string().min(1, "Obbligatorio"),
  planned_start: z.string().min(1, "Obbligatorio"),
  planned_end: z.string().min(1, "Obbligatorio"),
  cremation_location_id: z.number().optional(),
});

type FormValues = z.infer<typeof schema>;

export function CremationCycleFormPage() {
  const navigate = useNavigate();
  const createCycle = useCreateCremationCycle();
  const { data: locations } = useCompanyLocations();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      // I campi orario arrivano da <input type="time"> come "HH:MM" - il
      // backend (Pydantic time) accetta anche senza i secondi.
      const cycle = await createCycle.mutateAsync(values);
      navigate(`/cicli-cremazione/${cycle.id}`);
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuovo ciclo di cremazione</h1>
      </div>
      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <div className="field-row">
          <label>
            Data
            <input type="date" {...register("cycle_date")} />
          </label>
          <label>
            Inizio previsto
            <input type="time" {...register("planned_start")} />
          </label>
          <label>
            Fine prevista
            <input type="time" {...register("planned_end")} />
          </label>
        </div>
        {(errors.cycle_date || errors.planned_start || errors.planned_end) && (
          <p className="field-error">Data e orari sono obbligatori.</p>
        )}

        <label>
          Sede di cremazione
          <select
            {...register("cremation_location_id", {
              // A differenza dei campi condizionali di PickupFormPage (mai
              // registrati se il ramo non e' visibile, quindi restano
              // undefined da soli), questo select e' sempre montato: senza
              // questa conversione "" diventerebbe 0 (Number("")) invece di
              // restare assente, violando la FK opzionale.
              setValueAs: (v) => (v === "" ? undefined : Number(v)),
            })}
          >
            <option value="">Seleziona...</option>
            {locations?.map((loc) => (
              <option key={loc.id} value={loc.id}>
                {loc.name}
              </option>
            ))}
          </select>
        </label>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva ciclo"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/cicli-cremazione")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
