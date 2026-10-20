import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useVeterinarians } from "@/features/veterinarians/api";
import { useCollaborators } from "@/shared/references";

import { useCompanyLocations, usePractice, useTags, useUpdatePractice, type PracticeUpdateInput } from "./api";
import { practiceToUpdateInput } from "./practiceUpdateMapper";

const PICKUP_TYPE_OPTIONS: { value: PracticeUpdateInput["pickup_type"]; label: string }[] = [
  { value: "domicilio", label: "Domicilio" },
  { value: "sede_aziendale", label: "Sede aziendale" },
  { value: "veterinario", label: "Veterinario" },
  { value: "collaboratore", label: "Collaboratore" },
  { value: "altro", label: "Altro" },
];

// V1 (edit_page + fields_html, app.py:17293/15590): stesso form usato per
// completare/correggere una pratica gia' creata. NON espone qui line_items
// (Preventivo/Pagamento in V1) ne' ddt_number/ddt_pdf/signature_data
// (protetti anche in V1 da questo stesso form, vedi commento in
// practiceUpdateMapper.ts) - restano fuori scope di questa pagina per non
// rischiare la zona economica, esattamente come in V1. Struttura campi
// V1 reale: <div class="fields"> (griglia 2 colonne) con <div class="field">
// <label>/<input> come fratelli - non <label> che avvolge l'input
// raggruppato in "field-row" (classe che non esiste in V1, approssimazione
// V2 rimossa).
export function PracticeEditFormPage() {
  const { practiceId } = useParams();
  const navigate = useNavigate();
  const id = Number(practiceId);
  const { data: practice, isLoading, isError } = usePractice(id);
  const { data: locations } = useCompanyLocations();
  const { data: veterinarians } = useVeterinarians({});
  const { data: collaborators } = useCollaborators();
  const { data: tags } = useTags();
  const update = useUpdatePractice();

  const [form, setForm] = useState<PracticeUpdateInput | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !practice) return <p className="error-banner">Pratica non trovata.</p>;

  const values = form ?? practiceToUpdateInput(practice);

  function set<K extends keyof PracticeUpdateInput>(key: K, value: PracticeUpdateInput[K]) {
    setForm({ ...values, [key]: value });
  }

  function setAnimal(index: number, patch: Partial<NonNullable<PracticeUpdateInput["animals"]>[number]>) {
    const animals = [...(values.animals ?? [])];
    animals[index] = { ...animals[index], ...patch };
    set("animals", animals);
  }

  function toggleTag(tagId: number) {
    const current = values.tag_ids ?? [];
    set("tag_ids", current.includes(tagId) ? current.filter((t) => t !== tagId) : [...current, tagId]);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);
    try {
      await update.mutateAsync({ practiceId: id, input: values });
      navigate(`/pratiche/${id}`);
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  const ownerName = [practice.owner_snapshot?.first_name, practice.owner_snapshot?.last_name]
    .filter(Boolean)
    .join(" ") || (practice.owner_snapshot?.company_name as string | undefined) || "-";

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Modifica {practice.practice_number}</h1>
          <div className="sub">Completa o correggi i dati della pratica.</div>
        </div>
        <div className="actions">
          <button className="btn" form="practiceEditForm" disabled={update.isPending}>
            {update.isPending ? "Salvataggio..." : "Salva modifiche"}
          </button>
          <button className="btn-ghost" type="button" onClick={() => navigate(`/pratiche/${id}`)}>
            Annulla
          </button>
        </div>
      </div>

      {serverError && <p className="error-banner">{serverError}</p>}

      <form id="practiceEditForm" onSubmit={handleSubmit}>
        <div className="section">
          <h2>Richiesta</h2>
          <div className="fields">
            <div className="field">
              <label>
                Sede di destinazione
                <select
                  value={values.destination_branch_id}
                  onChange={(e) => set("destination_branch_id", Number(e.target.value))}
                >
                  {locations?.map((loc) => (
                    <option key={loc.id} value={loc.id}>
                      {loc.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="field">
              <label>
                Origine
                <select value={values.request_origin} onChange={(e) => set("request_origin", e.target.value)}>
                  <option value="Collaboratore">Collaboratore</option>
                  <option value="Consegna in sede">Consegna in sede</option>
                </select>
              </label>
            </div>
            <div className="field">
              <label>
                Servizio
                <select value={values.service_type} onChange={(e) => set("service_type", e.target.value)}>
                  <option value="Da decidere">Da decidere</option>
                  <option value="Cremazione singola">Cremazione singola</option>
                  <option value="Cremazione collettiva">Cremazione collettiva</option>
                </select>
              </label>
            </div>
          </div>
        </div>

        <div style={{ height: 14 }} />

        {/* V1 (SPEDITORE): il cliente non e' modificabile dopo la
            creazione in V2 (doc06 Addendum A - owner_snapshot e' legato al
            cliente al momento della creazione), mostrato sola lettura. */}
        <div className="section">
          <h2>Speditore</h2>
          <p className="sub">{ownerName} (non modificabile dopo la creazione della pratica).</p>
          <div className="fields">
            <div className="field">
              <label>
                Collaboratore
                <select
                  value={values.collaborator_id ?? ""}
                  onChange={(e) => set("collaborator_id", e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">Nessuno</option>
                  {collaborators?.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="field">
              <label>
                Veterinario di origine
                <select
                  value={values.origin_veterinarian_id ?? ""}
                  onChange={(e) => set("origin_veterinarian_id", e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">Nessuno</option>
                  {veterinarians?.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.short_name || v.clinic_name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        </div>

        <div style={{ height: 14 }} />

        <div className="section">
          <h2>Luogo di origine</h2>
          <div className="fields">
            <div className="field">
              <label>
                Tipo di ritiro
                <select value={values.pickup_type} onChange={(e) => set("pickup_type", e.target.value as PracticeUpdateInput["pickup_type"])}>
                  {PICKUP_TYPE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="field">
              <label>
                Veterinario (punto di affido)
                <select
                  value={values.veterinarian_id ?? ""}
                  onChange={(e) => set("veterinarian_id", e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">Nessuno</option>
                  {veterinarians?.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.short_name || v.clinic_name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="field full">
              <label>
                Indirizzo di ritiro
                <input value={values.pickup_address ?? ""} onChange={(e) => set("pickup_address", e.target.value || null)} />
              </label>
            </div>
            <div className="field">
              <label>
                Referente al ritiro
                <input
                  value={values.pickup_contact_name ?? ""}
                  onChange={(e) => set("pickup_contact_name", e.target.value || null)}
                />
              </label>
            </div>
            <div className="field">
              <label>
                Codice di provenienza
                <input value={values.provenance_code ?? ""} onChange={(e) => set("provenance_code", e.target.value || null)} />
              </label>
            </div>
          </div>
        </div>

        <div style={{ height: 14 }} />

        <div className="section">
          <h2>Animale</h2>
          {(values.animals ?? []).map((animal, index) => (
            <div key={index}>
              {index > 0 && <h3>Secondo animale</h3>}
              <div className="fields">
                <div className="field">
                  <label>
                    Nome
                    <input value={animal.name ?? ""} onChange={(e) => setAnimal(index, { name: e.target.value || null })} />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Specie
                    <input value={animal.species ?? ""} onChange={(e) => setAnimal(index, { species: e.target.value || null })} />
                  </label>
                </div>
                <div className="field full">
                  <label>
                    Razza
                    <input value={animal.breed ?? ""} onChange={(e) => setAnimal(index, { breed: e.target.value || null })} />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Peso stimato (kg)
                    <input
                      type="number"
                      step="0.1"
                      value={animal.estimated_weight_grams != null ? animal.estimated_weight_grams / 1000 : ""}
                      onChange={(e) =>
                        setAnimal(index, { estimated_weight_grams: e.target.value ? Math.round(Number(e.target.value) * 1000) : null })
                      }
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Microchip animale
                    <input value={animal.microchip ?? ""} onChange={(e) => setAnimal(index, { microchip: e.target.value || null })} />
                  </label>
                </div>
              </div>
              {(values.animals ?? []).length > 1 && (
                <button
                  type="button"
                  className="btn-ghost"
                  style={{ marginTop: 12 }}
                  onClick={() => set("animals", (values.animals ?? []).filter((_, i) => i !== index))}
                >
                  Rimuovi animale
                </button>
              )}
            </div>
          ))}
          {(values.animals ?? []).length < 2 && (
            <button
              type="button"
              className="btn-ghost"
              style={{ marginTop: 12 }}
              onClick={() =>
                set("animals", [
                  ...(values.animals ?? []),
                  { name: null, species: null, breed: null, age_years: null, age_months: null, estimated_weight_grams: null, microchip: null, cremation_type: null },
                ])
              }
            >
              + Aggiungi secondo animale
            </button>
          )}
        </div>

        <div style={{ height: 14 }} />

        <div className="section">
          <h2>Trasportatore</h2>
          <div className="fields">
            <div className="field">
              <label>
                Modalita' di trasporto
                <input value={values.transport_method ?? ""} onChange={(e) => set("transport_method", e.target.value || null)} />
              </label>
            </div>
            <div className="field">
              <label>
                Targa veicolo
                <input value={values.vehicle_plate ?? ""} onChange={(e) => set("vehicle_plate", e.target.value || null)} />
              </label>
            </div>
            <div className="field">
              <label>
                Modalita' di temperatura
                <input value={values.temperature_mode ?? ""} onChange={(e) => set("temperature_mode", e.target.value || null)} />
              </label>
            </div>
            <div className="field">
              <label>
                Numero colli
                <input
                  type="number"
                  value={values.package_count ?? ""}
                  onChange={(e) => set("package_count", e.target.value ? Number(e.target.value) : null)}
                />
              </label>
            </div>
            <div className="field">
              <label>
                Contenitore
                <input value={values.container_id ?? ""} onChange={(e) => set("container_id", e.target.value || null)} />
              </label>
            </div>
            <div className="field">
              <label>
                Lotto
                <input value={values.lot_number ?? ""} onChange={(e) => set("lot_number", e.target.value || null)} />
              </label>
            </div>
            <div className="field full">
              <label>
                Modalita' di trattamento
                <input value={values.treatment_method ?? ""} onChange={(e) => set("treatment_method", e.target.value || null)} />
              </label>
            </div>
            <div className="field">
              <label className="day-checkbox">
                <input
                  type="checkbox"
                  checked={values.delivery_at_clinic}
                  onChange={(e) => set("delivery_at_clinic", e.target.checked)}
                />
                Riconsegna in ambulatorio
              </label>
            </div>
            <div className="field">
              <label className="day-checkbox">
                <input
                  type="checkbox"
                  checked={values.delivery_at_home}
                  onChange={(e) => set("delivery_at_home", e.target.checked)}
                />
                Riconsegna a domicilio
              </label>
            </div>
          </div>
        </div>

        <div style={{ height: 14 }} />

        <div className="section">
          <h2>Documento e accettazione</h2>
          <div className="fields">
            <div className="field full">
              <label>
                Microchip pratica
                <input value={values.microchip ?? ""} onChange={(e) => set("microchip", e.target.value || null)} />
              </label>
            </div>
            <div className="field">
              <label>
                Documento del firmatario
                <input
                  value={values.signatory_identity_document_number ?? ""}
                  onChange={(e) => set("signatory_identity_document_number", e.target.value || null)}
                />
              </label>
            </div>
            <div className="field">
              <label>
                Data documento
                <input
                  type="date"
                  value={values.signatory_identity_document_date ?? ""}
                  onChange={(e) => set("signatory_identity_document_date", e.target.value || null)}
                />
              </label>
            </div>
            <div className="field full">
              <label>
                Luogo firma
                <input
                  value={values.signatory_signing_place ?? ""}
                  onChange={(e) => set("signatory_signing_place", e.target.value || null)}
                />
              </label>
            </div>
            <div className="field">
              <label className="day-checkbox">
                <input type="checkbox" checked={values.to_invoice} onChange={(e) => set("to_invoice", e.target.checked)} />
                Da fatturare
              </label>
            </div>
            <div className="field">
              <label className="day-checkbox">
                <input type="checkbox" checked={values.send_catalog} onChange={(e) => set("send_catalog", e.target.checked)} />
                Invia catalogo
              </label>
            </div>
            <div className="field">
              <label className="day-checkbox">
                <input type="checkbox" checked={values.send_estremi} onChange={(e) => set("send_estremi", e.target.checked)} />
                Invia estremi
              </label>
            </div>
            <div className="field">
              <label className="day-checkbox">
                <input
                  type="checkbox"
                  checked={values.voucher_requested}
                  onChange={(e) => set("voucher_requested", e.target.checked)}
                />
                Buono richiesto
              </label>
            </div>
            <div className="field">
              <label className="day-checkbox">
                <input type="checkbox" checked={values.use_voucher} onChange={(e) => set("use_voucher", e.target.checked)} />
                Usa buono
              </label>
            </div>
            {tags && tags.length > 0 && (
              <div className="field full">
                <label>Etichette</label>
                <div className="fields">
                  {tags.map((tag) => (
                    <div className="field" key={tag.id}>
                      <label className="day-checkbox">
                        <input type="checkbox" checked={(values.tag_ids ?? []).includes(tag.id)} onChange={() => toggleTag(tag.id)} />
                        {tag.label}
                      </label>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div style={{ height: 14 }} />

        <div className="section">
          <h2>Note</h2>
          <div className="fields">
            <div className="field full">
              <label>
                Note
                <textarea value={values.notes ?? ""} onChange={(e) => set("notes", e.target.value || null)} />
              </label>
            </div>
          </div>
        </div>

        <div className="actions" style={{ marginTop: 18 }}>
          <button className="btn" type="submit" disabled={update.isPending}>
            {update.isPending ? "Salvataggio..." : "Salva modifiche"}
          </button>
          <button className="btn-ghost" type="button" onClick={() => navigate(`/pratiche/${id}`)}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
