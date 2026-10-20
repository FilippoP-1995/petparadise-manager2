import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { BuildingIcon, CalendarIcon, ClipboardIcon, ClockIcon, HomeIcon, PawIcon, StethoscopeIcon, TruckIcon, UserIcon } from "@/app/icons";
import { useCompanyLocations } from "@/features/practices/api";
import { useVeterinarians } from "@/features/veterinarians/api";
import { CalendarCard, CollapsibleCalendarSection, TapCard } from "@/shared/CalendarEventCard";
import { ClientPicker } from "@/shared/ClientPicker";
import { useCalendarZones, useCollaborators } from "@/shared/references";
import type { Client } from "@/features/clients/api";

import { useCreatePickup } from "./api";

const animalSchema = z.object({
  name: z.string().optional(),
  species: z.string().optional(),
  breed: z.string().optional(),
  age_years: z.number().optional(),
  age_months: z.number().optional(),
  estimated_weight_grams: z.number().optional(),
  microchip: z.string().optional(),
});

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
  animals: z.array(animalSchema),
});

type FormValues = z.infer<typeof schema>;
export type PickupType = FormValues["pickup_type"];

// Estratta da PickupFormPage per essere riusabile anche embedded nel
// type-picker unificato "Nuovo evento" (V1 calendar_event_form) - stessa
// identica logica/campi. Markup/CSS presi 1:1 dal codice grafico reale di
// V1 per questo form (".calendar-form-v2": card vetro + chip icona colorato
// per campo, sezioni apri/chiudi tono colore) invece della griglia .fields
// generica usata dal resto dell'app - qui V1 usa davvero un linguaggio
// visivo diverso, non e' un'approssimazione. "Operatore"/"Preventivo" non
// hanno equivalente in PickupCreate (schemas/calendar_event.py) - gap di
// dominio disclosed, non ricostruibili senza cambiare il motore.
export function PickupEventForm({
  defaultPickupType = "domicilio",
  onCreated,
  onCancel,
}: {
  defaultPickupType?: PickupType;
  onCreated: () => void;
  onCancel?: () => void;
}) {
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
    defaultValues: { pickup_type: defaultPickupType, animals: [{}] },
  });
  const { fields, append, remove } = useFieldArray({ control, name: "animals" });
  const pickupType = watch("pickup_type");

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      await createPickup.mutateAsync({
        ...values,
        client_id: selectedClient?.id ?? null,
        animals: values.animals
          .filter((a) => a.name?.trim())
          // Il form raccoglie il peso in kg (stessa UX di Nuova pratica) -
          // solo qui convertito nei grammi realmente attesi da AnimalInput.
          .map((a) => ({ ...a, estimated_weight_grams: a.estimated_weight_grams ? Math.round(a.estimated_weight_grams * 1000) : undefined })),
      });
      onCreated();
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <form id="pickupForm" onSubmit={handleSubmit(onSubmit)}>
      <CalendarCard eyebrow="Dettagli evento">
        <div className="calendar-v2-row2">
          <TapCard icon={CalendarIcon} color="purple" label="Inizio">
            <input type="datetime-local" {...register("start_at")} />
          </TapCard>
          <TapCard icon={ClockIcon} color="blue" label="Fine">
            <input type="datetime-local" {...register("end_at")} />
          </TapCard>
        </div>
        {(errors.start_at || errors.end_at) && <p className="field-error">Data/ora obbligatorie.</p>}
      </CalendarCard>

      <CalendarCard eyebrow="Luogo del ritiro">
        <TapCard icon={TruckIcon} color="orange" label="Tipo di ritiro">
          <select {...register("pickup_type")}>
            <option value="sede_aziendale">Sede aziendale</option>
            <option value="domicilio">Domicilio</option>
            <option value="veterinario">Veterinario</option>
            <option value="collaboratore">Collaboratore</option>
            <option value="altro">Altro</option>
          </select>
        </TapCard>

        {pickupType === "sede_aziendale" && (
          <TapCard icon={HomeIcon} color="blue" label="Sede">
            <select {...register("pickup_location_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {locations?.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.name}
                </option>
              ))}
            </select>
          </TapCard>
        )}

        {pickupType === "domicilio" && (
          <>
            <TapCard icon={BuildingIcon} color="green" label="Zona">
              <select {...register("pickup_zone_id", { valueAsNumber: true })}>
                <option value="">Seleziona...</option>
                {zones?.map((zone) => (
                  <option key={zone.id} value={zone.id}>
                    {zone.name}
                  </option>
                ))}
              </select>
            </TapCard>
            <TapCard icon={BuildingIcon} color="green" label="Indirizzo">
              <input {...register("pickup_address")} />
            </TapCard>
          </>
        )}

        {pickupType === "veterinario" && (
          <TapCard icon={StethoscopeIcon} color="blue" label="Veterinario">
            <select {...register("veterinarian_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {veterinarians?.map((vet) => (
                <option key={vet.id} value={vet.id}>
                  {vet.clinic_name ?? vet.doctor_name}
                </option>
              ))}
            </select>
          </TapCard>
        )}

        {pickupType === "collaboratore" && (
          <TapCard icon={UserIcon} color="orange" label="Collaboratore">
            <select {...register("collaborator_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {collaborators?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </TapCard>
        )}

        {pickupType === "altro" && (
          <TapCard icon={UserIcon} color="orange" label="Referente">
            <input {...register("pickup_contact_name")} />
          </TapCard>
        )}
      </CalendarCard>

      {/* V1 (client_block, app.py:10908): sezione "Cliente / Proprietario"
          distinta - V2 lega il cliente a un'anagrafica reale (client_id),
          stesso ClientPicker gia' condiviso con Nuova pratica. */}
      <CollapsibleCalendarSection icon={UserIcon} color="purple" title="Cliente / Proprietario">
        <ClientPicker selectedClient={selectedClient} onSelect={setSelectedClient} />
      </CollapsibleCalendarSection>

      <CollapsibleCalendarSection icon={PawIcon} color="orange" title="Animali">
        <p className="sub">Aggiungi uno o più animali al ritiro.</p>
        {fields.map((field, index) => (
          <div key={field.id}>
            <div className="fields">
              <div className="field">
                <label>
                  Nome
                  <input {...register(`animals.${index}.name` as const)} />
                </label>
              </div>
              <div className="field">
                <label>
                  Specie
                  <input {...register(`animals.${index}.species` as const)} />
                </label>
              </div>
              <div className="field full">
                <label>
                  Razza
                  <input {...register(`animals.${index}.breed` as const)} />
                </label>
              </div>
              <div className="field">
                <label>
                  Peso stimato (kg)
                  <input type="number" step="0.1" {...register(`animals.${index}.estimated_weight_grams` as const, { valueAsNumber: true })} />
                </label>
              </div>
              <div className="field">
                <label>
                  Microchip
                  <input {...register(`animals.${index}.microchip` as const)} />
                </label>
              </div>
              <div className="field">
                <label>
                  Anni
                  <input type="number" {...register(`animals.${index}.age_years` as const, { valueAsNumber: true })} />
                </label>
              </div>
              <div className="field">
                <label>
                  Mesi
                  <input type="number" {...register(`animals.${index}.age_months` as const, { valueAsNumber: true })} />
                </label>
              </div>
            </div>
            {fields.length > 1 && (
              <button type="button" className="btn-ghost" onClick={() => remove(index)}>
                Rimuovi animale
              </button>
            )}
          </div>
        ))}
        <button type="button" className="calendar-add-appt-btn" onClick={() => append({})}>
          + Aggiungi animale
        </button>
      </CollapsibleCalendarSection>

      <CollapsibleCalendarSection icon={ClipboardIcon} color="gray" title="Note">
        <textarea {...register("notes")} />
      </CollapsibleCalendarSection>

      {serverError && <p className="error-banner">{serverError}</p>}

      <div className="calendar-v2-actions">
        <button className="btn" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Salvataggio..." : "Salva ritiro"}
        </button>
        {onCancel && (
          <button type="button" className="btn-ghost" onClick={onCancel}>
            Annulla
          </button>
        )}
      </div>
    </form>
  );
}

export function PickupFormPage() {
  const navigate = useNavigate();
  return (
    <main className="wrap calendar-form calendar-form-v2">
      <div className="titlebar">
        <div>
          <h1>Nuovo ritiro</h1>
          <p className="sub">Registra un nuovo ritiro da programmare.</p>
        </div>
        <div className="actions">
          <button className="btn" form="pickupForm">
            Salva ritiro
          </button>
        </div>
      </div>
      <PickupEventForm onCreated={() => navigate("/ritiri")} onCancel={() => navigate("/ritiri")} />
    </main>
  );
}
