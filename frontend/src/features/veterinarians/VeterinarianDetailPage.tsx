import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import {
  useDeactivateVeterinarian,
  useUpdateVeterinarian,
  useVeterinarian,
  type VeterinarianCreateInput,
} from "./api";

type HoursInput = NonNullable<VeterinarianCreateInput["hours"]>[number];

const DAY_LABELS = ["Lunedi'", "Martedi'", "Mercoledi'", "Giovedi'", "Venerdi'", "Sabato", "Domenica"];

function emptyDay(day_of_week: number): HoursInput {
  return { day_of_week, closed: false, morning_start: null, morning_end: null, afternoon_start: null, afternoon_end: null, notes: null };
}

// V1 usa <input type="time"> (formato "HH:MM") mentre il backend
// restituisce un time SQL ("HH:MM:SS") - stesso troncamento gia' usato
// altrove nel progetto per adattare i due formati senza cambiare il dato.
function toTimeInputValue(value: string | null | undefined): string {
  return (value ?? "").slice(0, 5);
}

// V1 (veterinarian_detail, app.py:15266-15292): titlebar nome+"Torna alla
// lista", .section "Anagrafica" (stessi campi del form "Aggiungi
// veterinario"), .section "Orari di apertura" (fasce orarie mattina/
// pomeriggio reali per giorno, non solo aperto/chiuso - il backend V2 le
// supporta gia' per intero, VeterinarianHoursInput ha closed/morning_start/
// morning_end/afternoon_start/afternoon_end/notes identici a V1), poi
// "Buoni" (tabella voucher) - quest'ultima NON portata qui: richiede un
// intero dominio voucher (veterinarian_vouchers, stati Maturato/Usato) che
// V2 non ha ancora, nessun dato reale da mostrare senza inventarlo. Idem
// Google Place ID/recupero automatico orari (hours_source, google_place_id):
// nessun campo equivalente nello schema V2, gap di dominio disclosed.
export function VeterinarianDetailPage() {
  const { veterinarianId } = useParams();
  const navigate = useNavigate();
  const id = Number(veterinarianId);
  const { data: vet, isLoading, isError } = useVeterinarian(id);
  const update = useUpdateVeterinarian();
  const deactivate = useDeactivateVeterinarian();
  const [form, setForm] = useState<VeterinarianCreateInput | null>(null);
  const [hoursForm, setHoursForm] = useState<HoursInput[] | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const [deactivateOpen, setDeactivateOpen] = useState(false);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !vet) return <p className="error-banner">Veterinario non trovato.</p>;

  const values = form ?? vet;
  const hours = hoursForm ?? Array.from({ length: 7 }, (_, day) => vet.hours.find((h) => h.day_of_week === day) ?? emptyDay(day));

  function field(key: keyof VeterinarianCreateInput) {
    const raw = values[key];
    return {
      value: typeof raw === "string" ? raw : "",
      onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...values, [key]: e.target.value }),
    };
  }

  function setDay(day: number, patch: Partial<HoursInput>) {
    const next = hours.map((h) => (h.day_of_week === day ? { ...h, ...patch } : h));
    setHoursForm(next);
  }

  async function handleSave() {
    setServerError(null);
    try {
      await update.mutateAsync({ veterinarianId: id, input: { ...values, hours } });
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  async function handleDeactivate() {
    await deactivate.mutateAsync(id);
    navigate("/veterinari");
  }

  const name = vet.short_name || vet.clinic_name || vet.doctor_name || "Veterinario";

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>{name}</h1>
          <div className="sub">Anagrafica veterinario</div>
        </div>
        <button className="btn-ghost" onClick={() => navigate("/veterinari")}>
          Torna alla lista
        </button>
      </div>

      <div className="section">
        <h2>Anagrafica</h2>
        <div className="fields">
          <div className="field">
            <label>
              Nome breve
              <input placeholder="Es. DEL PERO" {...field("short_name")} />
            </label>
          </div>
          <div className="field">
            <label>
              Nome completo
              <input {...field("clinic_name")} />
            </label>
          </div>
          <div className="field full">
            <label>
              Indirizzo
              <input {...field("address")} />
            </label>
          </div>
          <div className="field">
            <label>
              Comune
              <input {...field("city")} />
            </label>
          </div>
          <div className="field">
            <label>
              Telefono
              <input {...field("phone")} />
            </label>
          </div>
          <div className="field">
            <label>
              Medico
              <input {...field("doctor_name")} />
            </label>
          </div>
          <div className="field full">
            <label>
              Note
              <input {...field("notes")} />
            </label>
          </div>
        </div>

        {serverError && <p className="error-banner">{serverError}</p>}

        <div className="actions">
          <button className="btn" disabled={update.isPending} onClick={handleSave}>
            {update.isPending ? "Salvataggio..." : "Salva anagrafica"}
          </button>
          <button className="btn-ghost" onClick={() => setDeactivateOpen(true)}>
            Disattiva veterinario
          </button>
        </div>
      </div>

      <div style={{ height: 14 }} />

      <div className="section">
        <h2>Orari di apertura</h2>
        <p className="sub">Usati dal Percorso giornaliero per segnalare arrivi fuori orario.</p>
        <div className="fields">
          <div className="field">
            <label>
              Durata media ritiro (minuti)
              <input
                type="number"
                min={1}
                max={240}
                placeholder="10"
                value={values.service_duration_minutes ?? ""}
                onChange={(e) => setForm({ ...values, service_duration_minutes: e.target.value ? Number(e.target.value) : null })}
              />
            </label>
          </div>
        </div>
        <div style={{ height: 10 }} />
        {DAY_LABELS.map((label, day) => {
          const h = hours[day];
          return (
            <div className="tablebox" style={{ padding: "10px 12px", marginBottom: 8 }} key={label}>
              <label className="modern-check">
                <input type="checkbox" checked={h.closed} onChange={(e) => setDay(day, { closed: e.target.checked })} /> <b>{label}</b> — chiuso
              </label>
              <div className="fields" style={{ marginTop: 8 }}>
                <div className="field">
                  <label>
                    Mattina dalle
                    <input
                      type="time"
                      value={toTimeInputValue(h.morning_start)}
                      onChange={(e) => setDay(day, { morning_start: e.target.value || null })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    alle
                    <input
                      type="time"
                      value={toTimeInputValue(h.morning_end)}
                      onChange={(e) => setDay(day, { morning_end: e.target.value || null })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Pomeriggio dalle
                    <input
                      type="time"
                      value={toTimeInputValue(h.afternoon_start)}
                      onChange={(e) => setDay(day, { afternoon_start: e.target.value || null })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    alle
                    <input
                      type="time"
                      value={toTimeInputValue(h.afternoon_end)}
                      onChange={(e) => setDay(day, { afternoon_end: e.target.value || null })}
                    />
                  </label>
                </div>
                <div className="field full">
                  <label>
                    Note
                    <input
                      value={h.notes ?? ""}
                      placeholder="Es. chiuso per pausa pranzo, orario estivo..."
                      onChange={(e) => setDay(day, { notes: e.target.value || null })}
                    />
                  </label>
                </div>
              </div>
            </div>
          );
        })}
        <button className="btn" style={{ marginTop: 4 }} disabled={update.isPending} onClick={handleSave}>
          {update.isPending ? "Salvataggio..." : "Salva orari"}
        </button>
      </div>

      <ConfirmModal
        open={deactivateOpen}
        onClose={() => setDeactivateOpen(false)}
        onConfirm={handleDeactivate}
        title="Disattiva veterinario"
        description={`Disattivare ${name}?`}
        confirmLabel="Disattiva"
        danger
        isPending={deactivate.isPending}
      />
    </main>
  );
}
