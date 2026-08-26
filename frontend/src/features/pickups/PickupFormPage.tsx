import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { useCompanyLocations } from "@/features/practices/api";
import { useVeterinarians } from "@/features/veterinarians/api";
import { ClientPicker } from "@/shared/ClientPicker";
import { useCalendarZones, useCollaborators } from "@/shared/references";
import type { Client } from "@/features/clients/api";

import { useCreatePickup } from "./api";

const schema = z.object({
  start_at: z.string().min(1, "Obbligatorio"),
  end_at: z.string().min(1, "Obbligatorio"),
  pickup_type: z.enum(["sede_aziendale", "domicilio", "veterinario", "collaboratore", "altro"]),
  pickup_location_id: z.number().optional(),
  pickup_zone_id: z.number().optional(),
  pickup_address: z.string().optional(),
  veterinarian_id: z.number().optional(),
  collaborator_id: z.number().optional(),
  pickup_contact_name: z.string().optional(),
  notes: z.string().optional(),
  animals: z.array(z.object({ name: z.string().optional(), species: z.string().optional() })),
});

type FormValues = z.infer<typeof schema>;

export function PickupFormPage() {
  const navigate = useNavigate();
  const createPickup = useCreatePickup();
  const { data: locations } = useCompanyLocations();
  const { data: zones } = useCalendarZones();
  const { data: veterinarians } = useVeterinarians({});
  const { data: collaborators } = useCollaborators();
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    watch,
    control,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { pickup_type: "domicilio", animals: [{}] },
  });
  const { fields, append, remove } = useFieldArray({ control, name: "animals" });
  const pickupType = watch("pickup_type");

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      await createPickup.mutateAsync({
        ...values,
        client_id: selectedClient?.id ?? null,
        animals: values.animals.filter((a) => a.name?.trim()),
      });
      navigate("/ritiri");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuovo ritiro</h1>
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

        <div className="field full">
          <label>Cliente</label>
          <ClientPicker selectedClient={selectedClient} onSelect={setSelectedClient} />
        </div>

        <label>
          Tipo di ritiro
          <select {...register("pickup_type")}>
            <option value="sede_aziendale">Sede aziendale</option>
            <option value="domicilio">Domicilio</option>
            <option value="veterinario">Veterinario</option>
            <option value="collaboratore">Collaboratore</option>
            <option value="altro">Altro</option>
          </select>
        </label>

        {pickupType === "sede_aziendale" && (
          <label>
            Sede
            <select {...register("pickup_location_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {locations?.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {pickupType === "domicilio" && (
          <>
            <label>
              Zona
              <select {...register("pickup_zone_id", { valueAsNumber: true })}>
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
              <input {...register("pickup_address")} />
            </label>
          </>
        )}

        {pickupType === "veterinario" && (
          <label>
            Veterinario
            <select {...register("veterinarian_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {veterinarians?.map((vet) => (
                <option key={vet.id} value={vet.id}>
                  {vet.clinic_name ?? vet.doctor_name}
                </option>
              ))}
            </select>
          </label>
        )}

        {pickupType === "collaboratore" && (
          <label>
            Collaboratore
            <select {...register("collaborator_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {collaborators?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}

        {pickupType === "altro" && (
          <label>
            Referente
            <input {...register("pickup_contact_name")} />
          </label>
        )}

        <div className="field full">
          <label>Animali</label>
          {fields.map((field, index) => (
            <div className="field-row" key={field.id}>
              <input placeholder="Nome" {...register(`animals.${index}.name` as const)} />
              <input placeholder="Specie" {...register(`animals.${index}.species` as const)} />
              {fields.length > 1 && (
                <button type="button" className="btn-ghost" onClick={() => remove(index)}>
                  Rimuovi
                </button>
              )}
            </div>
          ))}
          <button type="button" className="btn-ghost" onClick={() => append({})}>
            + Aggiungi animale
          </button>
        </div>

        <label>
          Note
          <textarea {...register("notes")} />
        </label>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Salva ritiro"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/ritiri")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
