import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { ArchiveIcon, BuildingIcon, CalendarIcon, ClipboardIcon, ClockIcon, HomeIcon, PawIcon, ReceiptIcon, StethoscopeIcon, UserIcon, WalletIcon } from "@/app/icons";
import type { Client } from "@/features/clients/api";
import { useCompanyLocations, type Practice } from "@/features/practices/api";
import { useVeterinarians } from "@/features/veterinarians/api";
import { CalendarCard, CollapsibleCalendarSection, TapCard } from "@/shared/CalendarEventCard";
import { ClientPicker } from "@/shared/ClientPicker";
import { PracticePicker } from "@/shared/PracticePicker";
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
  preliminary_payment_status: z.string().optional(),
  preliminary_payment_amount_euro: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;
export type DeliveryType = FormValues["delivery_type"];

// Estratta da DeliveryFormPage per essere riusabile anche embedded nel
// type-picker unificato "Nuovo evento" (V1 calendar_event_form) - stessa
// identica logica/campi. Markup/CSS presi 1:1 dal codice grafico reale di
// V1 per questo form (".calendar-form-v2"), stesso discorso di
// PickupEventForm. Il campo piu' importante di V1 ("Animale *", ricerca
// pratica/animale/cliente) mancava del tutto qui pur essendo gia'
// disponibile come linked_practice_id in DeliveryCreate (schemas/
// calendar_event.py) - riusa lo stesso PracticePicker gia' condiviso con
// Nuova fattura. Stesso discorso per Cliente e Stato/Importo pagamento
// (preliminary_payment_*), gia' nel dominio ma prima non esposti in UI.
export function DeliveryEventForm({
  defaultDeliveryType = "sede_aziendale",
  onCreated,
  onCancel,
}: {
  defaultDeliveryType?: DeliveryType;
  onCreated: () => void;
  onCancel?: () => void;
}) {
  const createDelivery = useCreateDelivery();
  const { data: locations } = useCompanyLocations();
  const { data: zones } = useCalendarZones();
  const { data: veterinarians } = useVeterinarians({});
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [selectedPractice, setSelectedPractice] = useState<Practice | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { delivery_type: defaultDeliveryType } });
  const deliveryType = watch("delivery_type");

  async function onSubmit(values: FormValues) {
    setServerError(null);
    const { preliminary_payment_amount_euro, ...rest } = values;
    try {
      await createDelivery.mutateAsync({
        ...rest,
        client_id: selectedClient?.id ?? null,
        linked_practice_id: selectedPractice?.id ?? null,
        preliminary_payment_amount: preliminary_payment_amount_euro
          ? Math.round(Number(preliminary_payment_amount_euro.replace(",", ".")) * 100)
          : null,
      });
      onCreated();
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <form id="deliveryForm" onSubmit={handleSubmit(onSubmit)}>
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

      {/* V1 (delivery_animal_card, app.py:10929): "Animale *" - ricerca
          pratica/animale/cliente, il campo piu' importante della
          Riconsegna, un unico calendar-tap-card verde. */}
      <TapCard icon={PawIcon} color="green" label="Animale">
        <PracticePicker selectedPractice={selectedPractice} onSelect={setSelectedPractice} />
      </TapCard>

      <div style={{ height: 8 }} />

      <CollapsibleCalendarSection icon={UserIcon} color="purple" title="Cliente / Proprietario">
        <ClientPicker selectedClient={selectedClient} onSelect={setSelectedClient} />
      </CollapsibleCalendarSection>

      <CalendarCard eyebrow="Luogo riconsegna">
        <TapCard icon={ArchiveIcon} color="orange" label="Modalita'">
          <select {...register("delivery_type")}>
            <option value="sede_aziendale">Sede aziendale</option>
            <option value="domicilio">Domicilio</option>
            <option value="ambulatorio">Ambulatorio</option>
            <option value="altro">Altro</option>
          </select>
        </TapCard>

        {deliveryType === "sede_aziendale" && (
          <TapCard icon={HomeIcon} color="blue" label="Sede">
            <select {...register("delivery_location_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {locations?.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.name}
                </option>
              ))}
            </select>
          </TapCard>
        )}

        {deliveryType === "domicilio" && (
          <>
            <TapCard icon={BuildingIcon} color="green" label="Zona">
              <select {...register("delivery_zone_id", { valueAsNumber: true })}>
                <option value="">Seleziona...</option>
                {zones?.map((zone) => (
                  <option key={zone.id} value={zone.id}>
                    {zone.name}
                  </option>
                ))}
              </select>
            </TapCard>
            <TapCard icon={BuildingIcon} color="green" label="Indirizzo">
              <input {...register("delivery_address")} />
            </TapCard>
          </>
        )}

        {deliveryType === "ambulatorio" && (
          <TapCard icon={StethoscopeIcon} color="blue" label="Veterinario">
            <select {...register("delivery_veterinarian_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {veterinarians?.map((vet) => (
                <option key={vet.id} value={vet.id}>
                  {vet.clinic_name ?? vet.doctor_name}
                </option>
              ))}
            </select>
          </TapCard>
        )}

        {deliveryType === "altro" && (
          <TapCard icon={BuildingIcon} color="green" label="Indirizzo/dettagli">
            <input {...register("delivery_address")} />
          </TapCard>
        )}
      </CalendarCard>

      {/* V1 (delivery_info_block, app.py:10975): "Stato pagamento" +
          "Importo" - preliminary_payment_status/amount, gia' nel dominio
          DeliveryCreate ma prima mai esposti in UI. Stessi colori rosa
          reali di V1 (wallet/receipt). Resta un dato preliminare/
          informativo (V1 stesso: solo un promemoria prima della
          riconsegna) - non registra un pagamento reale, quindi non tocca
          il motore pagamenti. */}
      <CalendarCard eyebrow="Pagamento">
        <TapCard icon={WalletIcon} color="pink" label="Stato pagamento">
          <select {...register("preliminary_payment_status")}>
            <option value="">Non indicato</option>
            <option value="Da pagare">Da pagare</option>
            <option value="Pagato">Pagato</option>
          </select>
        </TapCard>
        <TapCard icon={ReceiptIcon} color="pink" label="Importo €">
          <input inputMode="decimal" placeholder="0,00" {...register("preliminary_payment_amount_euro")} />
        </TapCard>
      </CalendarCard>

      <CollapsibleCalendarSection icon={ClipboardIcon} color="gray" title="Note">
        <textarea {...register("notes")} />
      </CollapsibleCalendarSection>

      {serverError && <p className="error-banner">{serverError}</p>}

      <div className="calendar-v2-actions">
        <button className="btn" type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Salvataggio..." : "Salva riconsegna"}
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

export function DeliveryFormPage() {
  const navigate = useNavigate();
  return (
    <main className="wrap calendar-form calendar-form-v2">
      <div className="titlebar">
        <div>
          <h1>Nuova riconsegna</h1>
          <p className="sub">Registra una nuova riconsegna da programmare.</p>
        </div>
        <div className="actions">
          <button className="btn" form="deliveryForm">
            Salva riconsegna
          </button>
        </div>
      </div>
      <DeliveryEventForm onCreated={() => navigate("/riconsegne")} onCancel={() => navigate("/riconsegne")} />
    </main>
  );
}
