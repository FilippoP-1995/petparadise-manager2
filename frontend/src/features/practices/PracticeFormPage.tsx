import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useClient, type Client } from "@/features/clients/api";
import { useVeterinarians } from "@/features/veterinarians/api";
import { useCollaborators } from "@/shared/references";
import { ClientPicker } from "@/shared/ClientPicker";

import { useCompanyLocations, useCreatePractice, useTags, type PracticeCreateInput } from "./api";
import { usePracticeDraft } from "./usePracticeDraft";

const DRAFT_KEY = "new-practice";

const PICKUP_TYPE_OPTIONS: { value: PracticeCreateInput["pickup_type"]; label: string }[] = [
  { value: "domicilio", label: "Domicilio" },
  { value: "sede_aziendale", label: "Sede aziendale" },
  { value: "veterinario", label: "Veterinario" },
  { value: "collaboratore", label: "Collaboratore" },
  { value: "altro", label: "Altro" },
];

type AnimalDraft = NonNullable<PracticeCreateInput["animals"]>[number];

function emptyAnimal(): AnimalDraft {
  return { name: null, species: null, breed: null, age_years: null, age_months: null, estimated_weight_grams: null, microchip: null, cremation_type: null };
}

function defaultValues(): PracticeCreateInput {
  return {
    client_id: 0,
    destination_branch_id: 0,
    request_origin: "Collaboratore",
    service_type: "Da decidere",
    collaborator_id: null,
    veterinarian_id: null,
    origin_veterinarian_id: null,
    pickup_type: "domicilio",
    pickup_address: null,
    pickup_contact_name: null,
    provenance_code: null,
    microchip: null,
    notes: null,
    transport_method: null,
    vehicle_plate: null,
    temperature_mode: null,
    package_count: null,
    container_id: null,
    lot_number: null,
    treatment_method: null,
    delivery_at_clinic: false,
    delivery_at_home: false,
    signatory_identity_document_number: null,
    signatory_identity_document_date: null,
    signatory_signing_place: null,
    to_invoice: false,
    send_catalog: false,
    send_estremi: false,
    voucher_requested: false,
    use_voucher: false,
    no_whatsapp_message: false,
    signature_data: null,
    animals: [emptyAnimal()],
    line_items: [],
    tag_ids: [],
  };
}

// V1 (fields_html, app.py:15590-15725): stesso form usato per creazione e
// modifica (in creazione tutte le sezioni partono aperte). Qui: STESSE
// sezioni/ordine reali di V1 per i campi che il dominio V2 supporta
// (_PracticeMutableFields, schemas/practice.py:41) - Preventivo/Pagamento
// (line_items/economia) restano fuori esattamente come in
// PracticeEditFormPage, per non toccare la zona economica/motore
// pagamenti. "Operatore" e "Stato pratica" non sono parametri di
// creazione in V2 (doc09: "lo stato iniziale non e' mai un parametro di
// creazione" - decisione di dominio reale, non un'omissione UI) - gap
// disclosed, non ricostruibile senza cambiare il motore.
// "SPEDITORE": V1 permette di scrivere i dati di un cliente nuovo
// direttamente sul form della pratica; V2 lega owner_snapshot a un Cliente
// reale gia' esistente (client_id FK, build_owner_snapshot) - un cliente
// mai censito va prima creato in Clienti (link qui sotto), non e'
// un'omissione ma un vincolo reale di dominio V2.
export function PracticeFormPage() {
  const navigate = useNavigate();
  const createPractice = useCreatePractice();
  const { data: locations } = useCompanyLocations();
  const { data: veterinarians } = useVeterinarians({});
  const { data: collaborators } = useCollaborators();
  const { data: tags } = useTags();
  const [serverError, setServerError] = useState<string | null>(null);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const draft = usePracticeDraft<PracticeCreateInput>(DRAFT_KEY);
  const [promptDismissed, setPromptDismissed] = useState(false);
  const showRestorePrompt = draft.hasStoredDraft && !promptDismissed;

  const [values, setValues] = useState<PracticeCreateInput>(defaultValues());
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [clientError, setClientError] = useState(false);

  function set<K extends keyof PracticeCreateInput>(key: K, value: PracticeCreateInput[K]) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function selectClient(c: Client | null) {
    setSelectedClient(c);
    set("client_id", c?.id ?? 0);
    setClientError(false);
  }

  // Un draft ripristinato porta con se' solo client_id (un numero), non
  // l'oggetto Client da mostrare - va ricaricato per poter mostrare il
  // riepilogo "cliente selezionato".
  const [pendingRestoreClientId, setPendingRestoreClientId] = useState<number | null>(null);
  const { data: restoredClient } = useClient(pendingRestoreClientId ?? NaN);
  useEffect(() => {
    if (restoredClient && pendingRestoreClientId === restoredClient.id) {
      selectClient(restoredClient);
      setPendingRestoreClientId(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoredClient]);

  // doc10 'Bozza persistente': ogni modifica al form viene salvata
  // (debounced) - se il refresh/crash/chiusura accidentale accade, i dati
  // non spariscono (il problema reale gia' vissuto in V1 su questo stesso
  // form).
  useEffect(() => {
    if (showRestorePrompt) return;
    draft.save(values);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(values), showRestorePrompt]);

  function handleRestoreDraft() {
    const stored = draft.readDraft();
    if (stored) {
      setValues(stored);
      if (stored.client_id) setPendingRestoreClientId(stored.client_id);
    }
    setPromptDismissed(true);
  }

  function handleDiscardDraft() {
    draft.clearDraft();
    setPromptDismissed(true);
  }

  function setAnimal(index: number, patch: Partial<AnimalDraft>) {
    const animals = [...(values.animals ?? [])];
    animals[index] = { ...animals[index], ...patch };
    set("animals", animals);
  }

  // V1 (canvas firma, signature_page/fields_html): stesso algoritmo di
  // disegno gia' portato 1:1 in PracticeSignaturePage.tsx - qui inline
  // (facoltativo in creazione, come in V1) invece di un secondo
  // componente duplicato.
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const lastRef = useRef<{ x: number; y: number } | null>(null);
  const [signatureOpen, setSignatureOpen] = useState(false);
  const [hasDrawn, setHasDrawn] = useState(false);

  useEffect(() => {
    if (!signatureOpen) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    function resize() {
      const r = canvas!.getBoundingClientRect();
      const d = window.devicePixelRatio || 1;
      canvas!.width = r.width * d;
      canvas!.height = r.height * d;
      ctx!.setTransform(d, 0, 0, d, 0, 0);
      ctx!.lineWidth = 3;
      ctx!.lineCap = "round";
      ctx!.strokeStyle = "#1f1f1f";
    }
    function pos(e: MouseEvent | TouchEvent) {
      const r = canvas!.getBoundingClientRect();
      const t = "touches" in e ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    }
    function start(e: MouseEvent | TouchEvent) {
      drawingRef.current = true;
      lastRef.current = pos(e);
      e.preventDefault();
    }
    function move(e: MouseEvent | TouchEvent) {
      if (!drawingRef.current || !lastRef.current) return;
      const p = pos(e);
      ctx!.beginPath();
      ctx!.moveTo(lastRef.current.x, lastRef.current.y);
      ctx!.lineTo(p.x, p.y);
      ctx!.stroke();
      lastRef.current = p;
      setHasDrawn(true);
      e.preventDefault();
    }
    function end(e: MouseEvent | TouchEvent) {
      drawingRef.current = false;
      e.preventDefault();
    }
    resize();
    window.addEventListener("resize", resize);
    canvas.addEventListener("mousedown", start);
    canvas.addEventListener("mousemove", move);
    canvas.addEventListener("mouseup", end);
    canvas.addEventListener("mouseleave", end);
    canvas.addEventListener("touchstart", start, { passive: false });
    canvas.addEventListener("touchmove", move, { passive: false });
    canvas.addEventListener("touchend", end, { passive: false });
    return () => {
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("mousedown", start);
      canvas.removeEventListener("mousemove", move);
      canvas.removeEventListener("mouseup", end);
      canvas.removeEventListener("mouseleave", end);
      canvas.removeEventListener("touchstart", start);
      canvas.removeEventListener("touchmove", move);
      canvas.removeEventListener("touchend", end);
    };
  }, [signatureOpen]);

  function saveSignature() {
    const canvas = canvasRef.current;
    if (!canvas || !hasDrawn) return;
    set("signature_data", canvas.toDataURL("image/png"));
    setSignatureOpen(false);
  }

  function clearSignature() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasDrawn(false);
  }

  function toggleTag(tagId: number) {
    const current = values.tag_ids ?? [];
    set("tag_ids", current.includes(tagId) ? current.filter((t) => t !== tagId) : [...current, tagId]);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setServerError(null);
    if (!selectedClient) {
      setClientError(true);
      return;
    }
    setIsSubmitting(true);
    try {
      await createPractice.mutateAsync({
        ...values,
        animals: (values.animals ?? []).filter((a) => a.name?.trim() || a.species?.trim()),
      });
      // Il draft si cancella SOLO dopo un salvataggio reale confermato
      // (risposta 2xx) - mai su un semplice cambio pagina (doc10, regola
      // esplicita).
      draft.clearDraft();
      navigate("/pratiche");
    } catch (err) {
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>Nuova pratica</h1>
          <p className="sub">Inserisci subito i dati disponibili; potrai completarli in seguito.</p>
        </div>
        <div className="actions">
          <button className="btn" form="practiceCreateForm" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Crea pratica"}
          </button>
        </div>
      </div>

      {showRestorePrompt && (
        <div className="flash warning">
          <p>Hai una bozza non salvata di una pratica in corso di compilazione.</p>
          <div className="actions">
            <button className="btn" type="button" onClick={handleRestoreDraft}>
              Riprendi bozza
            </button>
            <button className="btn-ghost" type="button" onClick={handleDiscardDraft}>
              Scarta
            </button>
          </div>
        </div>
      )}

      {serverError && <p className="error-banner">{serverError}</p>}

      <form id="practiceCreateForm" onSubmit={handleSubmit}>
        <div className="section">
          <h2>Richiesta</h2>
          <div className="fields">
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
                Sede di destinazione
                <select value={values.destination_branch_id || ""} onChange={(e) => set("destination_branch_id", Number(e.target.value))}>
                  <option value="">Seleziona...</option>
                  {locations?.map((loc) => (
                    <option key={loc.id} value={loc.id}>
                      {loc.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        </div>

        <div style={{ height: 14 }} />

        {/* V1 (SPEDITORE, app.py:15711): il cliente nuovo si scrive qui
            direttamente; V2 lega owner_snapshot a un Cliente reale gia'
            esistente (client_id) - un cliente non censito va creato prima
            in Clienti, vincolo di dominio reale non aggirabile qui. */}
        <div className="section">
          <h2>Speditore</h2>
          <div className="fields">
            <div className="field full">
              <label>Cliente</label>
              <ClientPicker selectedClient={selectedClient} onSelect={selectClient} />
              {clientError && !selectedClient && <p className="field-error">Seleziona un cliente prima di salvare.</p>}
              <small className="sub">
                Cliente non ancora censito? <a href="/clienti/nuovo" target="_blank" rel="noreferrer">Crealo in Clienti</a>, poi cercalo qui.
              </small>
            </div>
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
                <select value={values.pickup_type} onChange={(e) => set("pickup_type", e.target.value as PracticeCreateInput["pickup_type"])}>
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
            <div className="field">
              <label className="day-checkbox">
                <input type="checkbox" checked={values.voucher_requested} onChange={(e) => set("voucher_requested", e.target.checked)} /> Buono richiesto
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
                    Microchip
                    <input value={animal.microchip ?? ""} onChange={(e) => setAnimal(index, { microchip: e.target.value || null })} />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Anni
                    <input
                      type="number"
                      value={animal.age_years ?? ""}
                      onChange={(e) => setAnimal(index, { age_years: e.target.value ? Number(e.target.value) : null })}
                    />
                  </label>
                </div>
                <div className="field">
                  <label>
                    Mesi
                    <input
                      type="number"
                      value={animal.age_months ?? ""}
                      onChange={(e) => setAnimal(index, { age_months: e.target.value ? Number(e.target.value) : null })}
                    />
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
            <button type="button" className="btn-ghost" style={{ marginTop: 12 }} onClick={() => set("animals", [...(values.animals ?? []), emptyAnimal()])}>
              + Aggiungi altro animale
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

        <div style={{ height: 14 }} />

        <div className="section">
          <h2>Etichette operative</h2>
          {tags && tags.length > 0 && (
            <div className="fields">
              {tags.map((tag) => (
                <div className="field" key={tag.id}>
                  <label className="day-checkbox">
                    <input
                      type="checkbox"
                      checked={(values.tag_ids ?? []).includes(tag.id)}
                      onChange={() => toggleTag(tag.id)}
                    />
                    {tag.label}
                  </label>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ height: 14 }} />

        {/* V1 (Firma proprietario, app.py:15721-15723): facoltativa in
            creazione, stesso canvas/algoritmo di PracticeSignaturePage. */}
        <div className="section">
          <h2>Firma proprietario</h2>
          <p className="sub">Facoltativa: potrai raccoglierla anche in seguito dalla pratica.</p>
          <p className="sub">{values.signature_data ? "Firma salvata." : "Nessuna firma."}</p>
          <div className="actions" style={{ marginTop: 12 }}>
            <button className="btn" type="button" onClick={() => setSignatureOpen(true)}>
              Apri area firma
            </button>
            {values.signature_data && (
              <button className="btn-ghost" type="button" onClick={() => set("signature_data", null)}>
                Rimuovi firma
              </button>
            )}
          </div>
          {signatureOpen && (
            <div className="payment-popover" onClick={(e) => { if (e.target === e.currentTarget) setSignatureOpen(false); }}>
              <div className="payment-dialog" style={{ maxWidth: 900 }}>
                <div className="titlebar">
                  <div>
                    <h2>Firma proprietario</h2>
                    <p className="sub">Fai firmare qui con il dito.</p>
                  </div>
                  <button className="btn-ghost" type="button" onClick={() => setSignatureOpen(false)}>
                    Chiudi
                  </button>
                </div>
                <canvas className="signature-pad" ref={canvasRef} style={{ height: "55vh" }} />
                <div className="actions" style={{ marginTop: 12 }}>
                  <button className="btn" type="button" onClick={saveSignature}>
                    Salva firma
                  </button>
                  <button className="btn-ghost" type="button" onClick={clearSignature}>
                    Cancella
                  </button>
                </div>
              </div>
            </div>
          )}
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
                <input type="checkbox" checked={values.use_voucher} onChange={(e) => set("use_voucher", e.target.checked)} />
                Usa buono
              </label>
            </div>
          </div>
        </div>

        <div className="actions" style={{ marginTop: 18 }}>
          <button className="btn" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Salvataggio..." : "Crea pratica"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/pratiche")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
