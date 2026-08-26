import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { useCreateVeterinarian } from "./api";

const DAY_LABELS = ["Lunedi'", "Martedi'", "Mercoledi'", "Giovedi'", "Venerdi'", "Sabato", "Domenica"];

const schema = z
  .object({
    clinic_name: z.string().optional(),
    doctor_name: z.string().optional(),
    phone: z.string().optional(),
    address: z.string().optional(),
    city: z.string().optional(),
    days: z.array(z.boolean()).length(7),
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
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { days: Array(7).fill(false) } });

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      const hours = values.days
        .map((open, day_of_week) => ({ day_of_week, closed: !open }))
        .filter((_, day_of_week) => values.days[day_of_week] !== undefined);
      await createVeterinarian.mutateAsync({
        clinic_name: values.clinic_name,
        doctor_name: values.doctor_name,
        phone: values.phone,
        address: values.address,
        city: values.city,
        hours,
      });
      navigate("/veterinari");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuovo veterinario</h1>
      </div>
      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <div className="field-row">
          <label>
            Clinica
            <input {...register("clinic_name")} />
          </label>
          <label>
            Medico
            <input {...register("doctor_name")} />
          </label>
        </div>
        {errors.clinic_name && <p className="field-error">{errors.clinic_name.message}</p>}
        <div className="field-row">
          <label>
            Telefono
            <input {...register("phone")} />
          </label>
          <label>
            Citta'
            <input {...register("city")} />
          </label>
        </div>
        <label>
          Indirizzo
          <input {...register("address")} />
        </label>

        <fieldset className="hours-grid">
          <legend>Giorni di apertura</legend>
          {DAY_LABELS.map((label, index) => (
            <label key={label} className="day-checkbox">
              <input type="checkbox" {...register(`days.${index}` as const)} />
              {label}
            </label>
          ))}
        </fieldset>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva veterinario"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/veterinari")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
