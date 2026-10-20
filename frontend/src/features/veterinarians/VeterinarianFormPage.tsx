import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { useCreateVeterinarian } from "./api";

const schema = z
  .object({
    short_name: z.string().optional(),
    clinic_name: z.string().optional(),
    doctor_name: z.string().optional(),
    phone: z.string().optional(),
    address: z.string().optional(),
    city: z.string().optional(),
    notes: z.string().optional(),
  })
  .refine((data) => data.clinic_name || data.doctor_name, {
    message: "Serve almeno il nome della clinica o del medico.",
    path: ["clinic_name"],
  });

type FormValues = z.infer<typeof schema>;

export function VeterinarianFormPage() {
  const navigate = useNavigate();
  const createVeterinarian = useCreateVeterinarian();
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      await createVeterinarian.mutateAsync({
        short_name: values.short_name,
        clinic_name: values.clinic_name,
        doctor_name: values.doctor_name,
        phone: values.phone,
        address: values.address,
        city: values.city,
        notes: values.notes,
      });
      navigate("/veterinari");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    // V1 (veterinarians_page, app.py:15263): <section class="section"><h2>
    // Aggiungi veterinario</h2><form><div class="fields"> - stessi 7 campi
    // reali in un'unica griglia 2 colonne, non <form class="card"> con
    // "field-row" manuali. "Giorni di apertura" non esiste nel form di
    // creazione di V1 (solo nel dettaglio, dopo la creazione, come fasce
    // orarie complete) - il fieldset "hours-grid"/"days" precedentemente
    // presente qui era una sezione intera in piu' rispetto a V1 (con
    // classi CSS "hours-grid"/"day-checkbox" inventate, assenti in
    // app.py), rimosso: gli orari si configurano dal dettaglio veterinario
    // dopo la creazione, esattamente come in V1.
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuovo veterinario</h1>
      </div>
      <div className="section">
        <h2>Aggiungi veterinario</h2>
        <form onSubmit={handleSubmit(onSubmit)}>
          <div className="fields">
            <div className="field">
              <label>
                Nome breve
                <input placeholder="Es. DEL PERO" {...register("short_name")} />
              </label>
            </div>
            <div className="field">
              <label>
                Nome completo
                <input {...register("clinic_name")} />
              </label>
            </div>
            {errors.clinic_name && <p className="field-error">{errors.clinic_name.message}</p>}
            <div className="field full">
              <label>
                Indirizzo
                <input {...register("address")} />
              </label>
            </div>
            <div className="field">
              <label>
                Comune
                <input {...register("city")} />
              </label>
            </div>
            <div className="field">
              <label>
                Telefono
                <input {...register("phone")} />
              </label>
            </div>
            <div className="field">
              <label>
                Medico
                <input {...register("doctor_name")} />
              </label>
            </div>
            <div className="field full">
              <label>
                Note
                <input {...register("notes")} />
              </label>
            </div>
          </div>

          {serverError && <p className="error-banner">{serverError}</p>}

          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn" type="submit" disabled={isSubmitting}>
              {isSubmitting ? "Salvataggio..." : "Salva veterinario"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => navigate("/veterinari")}>
              Annulla
            </button>
          </div>
        </form>
      </div>
    </main>
  );
}
