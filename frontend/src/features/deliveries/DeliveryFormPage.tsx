import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { useCompanyLocations } from "@/features/practices/api";
import { useVeterinarians } from "@/features/veterinarians/api";
import { useCalendarZones } from "@/shared/references";

import { useCreateDelivery } from "./api";

const schema = z.object({
  start_at: z.string().min(1, "Obbligatorio"),
  end_at: z.string().min(1, "Obbligatorio"),
  delivery_type: z.enum(["ambulatorio", "domicilio", "sede_aziendale", "altro"]),
  delivery_veterinarian_id: z.number().optional(),
  delivery_zone_id: z.number().optional(),
  delivery_location_id: z.number().optional(),
  delivery_address: z.string().optional(),
  notes: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export function DeliveryFormPage() {
  const navigate = useNavigate();
  const createDelivery = useCreateDelivery();
  const { data: locations } = useCompanyLocations();
  const { data: zones } = useCalendarZones();
  const { data: veterinarians } = useVeterinarians({});
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { delivery_type: "sede_aziendale" } });
  const deliveryType = watch("delivery_type");

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      await createDelivery.mutateAsync(values);
      navigate("/riconsegne");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuova riconsegna</h1>
      </div>
      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <div className="field-row">
          <label>
            Inizio
            <input type="datetime-local" {...register("start_at")} />
          </label>
          <label>
            Fine
            <input type="datetime-local" {...register("end_at")} />
          </label>
        </div>
        {(errors.start_at || errors.end_at) && <p className="field-error">Data/ora obbligatorie.</p>}

        <label>
          Modalita'
          <select {...register("delivery_type")}>
            <option value="sede_aziendale">Sede aziendale</option>
            <option value="domicilio">Domicilio</option>
            <option value="ambulatorio">Ambulatorio</option>
            <option value="altro">Altro</option>
          </select>
        </label>

        {deliveryType === "sede_aziendale" && (
          <label>
            Sede
            <select {...register("delivery_location_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {locations?.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {deliveryType === "domicilio" && (
          <>
            <label>
              Zona
              <select {...register("delivery_zone_id", { valueAsNumber: true })}>
                <option value="">Seleziona...</option>
                {zones?.map((zone) => (
                  <option key={zone.id} value={zone.id}>
                    {zone.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Indirizzo
              <input {...register("delivery_address")} />
            </label>
          </>
        )}

        {deliveryType === "ambulatorio" && (
          <label>
            Veterinario
            <select {...register("delivery_veterinarian_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {veterinarians?.map((vet) => (
                <option key={vet.id} value={vet.id}>
                  {vet.clinic_name ?? vet.doctor_name}
                </option>
              ))}
            </select>
          </label>
        )}

        {deliveryType === "altro" && (
          <label>
            Indirizzo/dettagli
            <input {...register("delivery_address")} />
          </label>
        )}

        <label>
          Note
          <textarea {...register("notes")} />
        </label>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva riconsegna"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/riconsegne")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
