import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useState } from "react";
import { useForm, useFieldArray } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";

import { useClient, type Client } from "@/features/clients/api";
import { ClientPicker } from "@/shared/ClientPicker";

import { useCompanyLocations, useCreatePractice } from "./api";
import { usePracticeDraft } from "./usePracticeDraft";

// Validazione immediata/UX lato client (doc10): mai sostitutiva della
// validazione server-side reale (domain/practice/rules.py).
const schema = z.object({
  client_id: z.number({ invalid_type_error: "Seleziona un cliente" }),
  destination_branch_id: z.number({ invalid_type_error: "Seleziona una sede" }),
  request_origin: z.enum(["Collaboratore", "Consegna in sede"]),
  service_type: z.enum(["Da decidere", "Cremazione singola", "Cremazione collettiva"]),
  notes: z.string().optional(),
  animals: z.array(z.object({ name: z.string().optional(), species: z.string().optional() })),
});

type FormValues = z.infer<typeof schema>;

const DRAFT_KEY = "new-practice";

export function PracticeFormPage() {
  const navigate = useNavigate();
  const createPractice = useCreatePractice();
  const { data: locations } = useCompanyLocations();
  const [serverError, setServerError] = useState<string | null>(null);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const draft = usePracticeDraft<FormValues>(DRAFT_KEY);
  const [promptDismissed, setPromptDismissed] = useState(false);
  const showRestorePrompt = draft.hasStoredDraft && !promptDismissed;

  const {
    register,
    handleSubmit,
    watch,
    reset,
    setValue,
    control,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { request_origin: "Collaboratore", service_type: "Da decidere", animals: [{}] },
  });
  const { fields, append, remove } = useFieldArray({ control, name: "animals" });

  function selectClient(c: Client | null) {
    setSelectedClient(c);
    setValue("client_id", c?.id as number, { shouldValidate: false });
  }

  // Un draft ripristinato porta con se' solo client_id (un numero), non
  // l'oggetto Client da mostrare - va ricaricato per poter mostrare il
  // riepilogo "cliente selezionato" e sbloccare il submit (che dipende da
  // selectedClient, non solo dal valore nascosto nello schema).
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
  const watched = watch();
  useEffect(() => {
    // Mentre il prompt di ripristino e' visibile, il form mostra ancora i
    // valori di default (vuoti) - un autosave qui sovrascriverebbe la
    // bozza reale prima ancora che l'utente possa scegliere di ripristinarla.
    if (showRestorePrompt) return;
    draft.save(watched);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(watched), showRestorePrompt]);

  function handleRestoreDraft() {
    const stored = draft.readDraft();
    if (stored) {
      reset(stored);
      if (stored.client_id) setPendingRestoreClientId(stored.client_id);
    }
    setPromptDismissed(true);
  }

  function handleDiscardDraft() {
    draft.clearDraft();
    setPromptDismissed(true);
  }

  async function onSubmit(values: FormValues) {
    setServerError(null);
    try {
      await createPractice.mutateAsync({
        ...values,
        animals: values.animals.filter((a) => a.name?.trim()),
        line_items: [],
        tag_ids: [],
        pickup_type: "domicilio",
        delivery_at_clinic: false,
        delivery_at_home: false,
        to_invoice: false,
        send_catalog: false,
        send_estremi: false,
        voucher_requested: false,
        use_voucher: false,
        no_whatsapp_message: false,
      });
      // Il draft si cancella SOLO dopo un salvataggio reale confermato
      // (risposta 2xx) - mai su un semplice cambio pagina (doc10, regola
      // esplicita).
      draft.clearDraft();
      navigate("/pratiche");
    } catch (err) {
      // Errore di rete/API: il draft resta in localStorage, i dati gia'
      // compilati nel form non vengono toccati (react-hook-form non
      // svuota i campi su un submit fallito).
      setServerError(err instanceof Error ? err.message : "Errore nel salvataggio");
    }
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Nuova pratica</h1>
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

      <form className="card" onSubmit={handleSubmit(onSubmit)}>
        <div className="field full">
          <label>Cliente</label>
          <ClientPicker selectedClient={selectedClient} onSelect={selectClient} />
          {errors.client_id && !selectedClient && <p className="field-error">Seleziona un cliente prima di salvare.</p>}
        </div>

        <div className="field-row">
          <label>
            Sede di destinazione
            <select {...register("destination_branch_id", { valueAsNumber: true })}>
              <option value="">Seleziona...</option>
              {locations?.map((loc) => (
                <option key={loc.id} value={loc.id}>
                  {loc.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Origine
            <select {...register("request_origin")}>
              <option value="Collaboratore">Collaboratore</option>
              <option value="Consegna in sede">Consegna in sede</option>
            </select>
          </label>
        </div>
        {errors.destination_branch_id && <p className="field-error">Seleziona una sede.</p>}

        <label>
          Servizio
          <select {...register("service_type")}>
            <option value="Da decidere">Da decidere</option>
            <option value="Cremazione singola">Cremazione singola</option>
            <option value="Cremazione collettiva">Cremazione collettiva</option>
          </select>
        </label>

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
          <button type="submit" disabled={isSubmitting || !selectedClient}>
            {isSubmitting ? "Salvataggio..." : "Salva pratica"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => navigate("/pratiche")}>
            Annulla
          </button>
        </div>
      </form>
    </main>
  );
}
