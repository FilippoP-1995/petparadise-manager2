import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { PhoneIcon, MessageIcon } from "@/app/icons";
import { PracticePaymentsSection } from "@/features/payments/PracticePaymentsSection";
import { usePracticeReconciliation } from "@/features/payments/api";
import { useVeterinarians } from "@/features/veterinarians/api";
import { formatMoney } from "@/shared/money";
import { ConfirmModal } from "@/shared/ui/ConfirmModal";

import { useCompanyLocations, usePractice, usePracticeHistory, useTags, useTransitionPractice, useTrashPractice, useUpdatePractice, type PracticeStatusValue } from "./api";
import { practiceToUpdateInput } from "./practiceUpdateMapper";

// V1 (dettaglio pratica: cronologia, event_type leggibile): stessa
// etichetta per tipo di evento, qui derivata dalla action tecnica scritta
// da ogni service nell'audit_log unificato (doc06) invece che da una
// tabella practice_history con event_type gia' in italiano.
const HISTORY_ACTION_LABELS: Record<string, string> = {
  created: "Pratica creata",
  practice_created: "Pratica creata",
  field_changed: "Dato modificato",
  state_changed: "Cambio stato",
  state_corrected: "Correzione stato",
  trashed: "Spostata nel Cestino",
  restored: "Ripristinata dal Cestino",
  total_overridden: "Totale corretto manualmente",
  total_override_cleared: "Correzione totale rimossa",
  owner_notified: "Proprietario avvisato",
  collaborator_billed: "Fatturato al collaboratore",
  // V1 (signature_submit, app.py:17445): stessa etichetta scritta in
  // practice_history quando la firma viene salvata/rimossa.
  signature_changed: "Firma proprietario",
};

function historyActionLabel(action: string): string {
  return HISTORY_ACTION_LABELS[action] ?? action;
}

const STATUS_LABELS: Record<string, string> = {
  ritirato: "Ritirato",
  in_programma: "In programma",
  cremato: "Cremato",
  da_consegnare: "Da consegnare",
  consegnato: "Consegnato",
  smaltito: "Smaltito",
};

// V1 (dettaglio pratica, kv "Speditore"): nome dal proprietario, o ragione
// sociale se e' un'azienda - stesso ordine di _owner_name gia' usato in
// dashboard_service.py, qui riletto da owner_snapshot (gia' incluso nella
// risposta pratica, nessuna nuova chiamata).
function ownerName(snapshot: Record<string, unknown> | null | undefined): string {
  if (!snapshot) return "";
  const company = snapshot.company_name;
  if (typeof company === "string" && company) return company;
  const first = typeof snapshot.first_name === "string" ? snapshot.first_name : "";
  const last = typeof snapshot.last_name === "string" ? snapshot.last_name : "";
  return [first, last].filter(Boolean).join(" ");
}

function ownerPhone(snapshot: Record<string, unknown> | null | undefined): string {
  const phone = snapshot?.phone;
  return typeof phone === "string" ? phone : "";
}

function snapshotStr(snapshot: Record<string, unknown> | null | undefined, key: string): string {
  const value = snapshot?.[key];
  return typeof value === "string" ? value : "";
}

// V1 (owner_address_display, app.py:17225-17227): "{via}, {cap} {citta}
// ({provincia})" - stessi campi reali di owner_snapshot, parti vuote
// scartate senza lasciare virgole doppie.
function ownerAddressDisplay(snapshot: Record<string, unknown> | null | undefined): string {
  const zip = snapshotStr(snapshot, "zip");
  const city = snapshotStr(snapshot, "city");
  const province = snapshotStr(snapshot, "province");
  let cityLine = [zip, city].filter(Boolean).join(" ");
  if (province) cityLine = `${cityLine} (${province})`.trim();
  const street = snapshotStr(snapshot, "street") || snapshotStr(snapshot, "address");
  return [street, cityLine].filter(Boolean).join(", ");
}

// V1 (wa_digits, app.py:16016): stessa normalizzazione (prefisso 39 se
// manca, rimozione 00 iniziale) - un numero con almeno una cifra genera
// sempre un link WhatsApp valido.
function waDigits(phone: string): string {
  let digits = phone.replace(/\D+/g, "");
  if (digits.startsWith("00")) digits = digits.slice(2);
  if (digits && !digits.startsWith("39")) digits = "39" + digits;
  return digits;
}

// V1 (phone_action_buttons, app.py:16043): numero + bottone chiama (tel:)
// + bottone WhatsApp (wa.me), non testo semplice.
function PhoneActions({ phone }: { phone: string }) {
  const trimmed = phone.trim();
  if (!trimmed) return null;
  const tel = trimmed.replace(/[^0-9+]/g, "");
  const wa = waDigits(trimmed);
  return (
    <>
      {trimmed}{" "}
      <a className="icon-btn phone-action-btn call-btn" href={`tel:${tel}`} aria-label="Chiama">
        <PhoneIcon />
      </a>{" "}
      {wa && (
        <a className="icon-btn phone-action-btn whatsapp-btn" href={`https://wa.me/${wa}`} target="_blank" rel="noopener noreferrer" aria-label="Apri chat WhatsApp">
          <MessageIcon />
        </a>
      )}
    </>
  );
}

function animalAge(years?: number | null, months?: number | null): string {
  const parts: string[] = [];
  if (years) parts.push(`${years} ${years === 1 ? "anno" : "anni"}`);
  if (months) parts.push(`${months} ${months === 1 ? "mese" : "mesi"}`);
  return parts.length > 0 ? `Età: ${parts.join(", ")}` : "Età non indicata";
}

export function PracticeDetailPage() {
  const { practiceId } = useParams();
  const navigate = useNavigate();
  const id = Number(practiceId);
  const { data: practice, isLoading, isError } = usePractice(id);
  const { data: history } = usePracticeHistory(id);
  const { data: reconciliation } = usePracticeReconciliation(id);
  const { data: locations } = useCompanyLocations();
  const { data: veterinarians } = useVeterinarians({});
  const { data: tags } = useTags();
  const transition = useTransitionPractice();
  const trash = useTrashPractice();
  const update = useUpdatePractice();
  const [actionError, setActionError] = useState<string | null>(null);
  const [trashModalOpen, setTrashModalOpen] = useState(false);

  if (isLoading) return <p className="loading">Caricamento...</p>;
  if (isError || !practice) return <p className="error-banner">Pratica non trovata.</p>;

  // Copia narrowed: TS non propaga il narrowing di `practice` dentro le
  // closure annidate sotto (handleStatusChange/handleTrash).
  const currentPractice = practice;

  // V1 (status_badges, app.py:14704-14705): la cella Stato di Riepilogo E'
  // un <select class="inline-state-select"> che invia direttamente il
  // cambio stato, non un bottone titlebar "Porta a prossimo stato" (che
  // in V1 non esiste su questa pagina). Il motore V2 (validate_workflow_
  // transition) resta piu' severo di V1 (solo transizioni valide, mai un
  // salto libero) - qui la UI e' quella V1, la convalida resta quella V2:
  // un tentativo non valido torna un errore reale invece di essere accettato.
  async function handleStatusChange(targetStatus: PracticeStatusValue) {
    if (targetStatus === currentPractice.status) return;
    setActionError(null);
    try {
      await transition.mutateAsync({ practiceId: id, targetStatus });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  async function handleTrash() {
    await trash.mutateAsync(id);
    navigate("/pratiche");
  }

  // V1 (signature_page overlay "Rimuovi firma", app.py:15721): stesso
  // mapper sicuro del form di modifica - solo signature_data cambia.
  async function handleRemoveSignature() {
    setActionError(null);
    try {
      await update.mutateAsync({ practiceId: id, input: { ...practiceToUpdateInput(currentPractice), signature_data: null } });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Operazione non riuscita");
    }
  }

  const firstAnimal = practice.animals[0];
  const owner = ownerName(practice.owner_snapshot);
  const phone = ownerPhone(practice.owner_snapshot);
  const createdDate = new Date(practice.created_at).toLocaleString("it-IT");
  const isPaid = reconciliation?.status === "pagata";
  const tagLabels = (practice.tags ?? [])
    .map((tagId) => tags?.find((t) => t.id === tagId)?.label)
    .filter((x): x is string => Boolean(x));
  const locationName = locations?.find((loc) => loc.id === practice.destination_branch_id)?.name;
  const vetName = veterinarians?.find((v) => v.id === practice.veterinarian_id)?.short_name
    || veterinarians?.find((v) => v.id === practice.veterinarian_id)?.clinic_name;

  return (
    // V1 (dettaglio pratica): titolo "{numero} - {animale}" + sotto-riga
    // "Creata il ..." + azioni ghost nel titlebar, corpo in due colonne
    // (.practice-layout: contenuto/.section a sinistra, cronologia a
    // destra) - non piu' un'unica card piatta con <p><strong>.
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>
            {practice.practice_number} - {firstAnimal?.name ?? "Animale da inserire"}
          </h1>
          <div className="sub">Creata il {createdDate}</div>
        </div>
        {/* V1 (dettaglio pratica, app.py:17244+17260): il template
            costruisce anche un bottone "Firma su telefono" qui, ma un
            .replace() finale lo rimuove sempre - il titlebar realmente
            renderizzato ha SOLO questi due bottoni. Nessun bottone "Porta
            a prossimo stato": il cambio stato in V1 avviene dalla cella
            Stato di Riepilogo (vedi sotto), non dal titlebar. */}
        <div className="actions">
          <button className="btn-ghost" onClick={() => navigate("/pratiche")}>
            ← Torna alla pagina precedente
          </button>
          <button className="btn-ghost" onClick={() => navigate(`/pratiche/${currentPractice.id}/modifica`)}>
            Modifica dati
          </button>
        </div>
      </div>

      {actionError && <p className="error-banner">{actionError}</p>}

      {/* V1 (dettaglio pratica): .practice-layout a 2 colonne, cronologia
          modifiche nell'aside a destra - qui letta dall'audit_log
          unificato gia' scritto da ogni service (usePracticeHistory),
          non un dominio inventato. */}
      <section className="grid practice-layout">
        <div className="grid">
          <div className="section">
            <h2>Riepilogo</h2>
            <div className="kvs">
              {/* V1 (dettaglio pratica, app.py:17248/status_badges): Stato +
                  le etichette nella STESSA cella, non una cella "Tag"
                  separata. Lo stato e' un <select> inline che invia
                  direttamente il cambio (V1: form/inline-state-select),
                  non un badge di sola lettura - vedi handleStatusChange:
                  la UI e' quella V1, la convalida resta quella del motore
                  V2 (transizioni valide, non un salto libero). */}
              <div className="kv">
                <small>Stato</small>
                <select
                  className={`inline-state-select practice-status badge practice-status-${practice.status}`}
                  value={practice.status}
                  disabled={transition.isPending}
                  onChange={(e) => handleStatusChange(e.target.value as PracticeStatusValue)}
                >
                  {Object.entries(STATUS_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                {tagLabels.map((label) => (
                  <span className="badge" key={label} style={{ marginLeft: 4 }}>
                    {label}
                  </span>
                ))}
              </div>
              {/* V1: "Totale pagato"/"Totale da pagare" + l'importo REALE
                  pagato o dovuto (mai il preventivo) - dato dalla stessa
                  riconciliazione gia' mostrata sotto in Pagamenti, non un
                  secondo calcolo parallelo. */}
              <div className="kv">
                <small>{isPaid ? "Totale pagato" : "Totale da pagare"}</small>
                <b>{formatMoney(isPaid ? (reconciliation?.paid_total_cents ?? 0) : (reconciliation?.residual_cents ?? practice.effective_total_cents))}</b>
              </div>
              {/* V1 (dettaglio pratica, app.py:17225-17228/17248): nome +
                  DUE numeri (ognuno con bottoni chiama/WhatsApp, non testo
                  semplice) + indirizzo completo + codice fiscale - tutti
                  campi reali gia' presenti in owner_snapshot
                  (build_owner_snapshot, domain/practice/rules.py), prima
                  non mostrati. */}
              <div className="kv">
                <small>Speditore</small>
                {owner || "-"}
                {phone && (
                  <>
                    <br />
                    <PhoneActions phone={phone} />
                  </>
                )}
                {snapshotStr(practice.owner_snapshot, "phone_2") && (
                  <>
                    <br />
                    <PhoneActions phone={snapshotStr(practice.owner_snapshot, "phone_2")} />
                  </>
                )}
                {ownerAddressDisplay(practice.owner_snapshot) && (
                  <>
                    <br />
                    {ownerAddressDisplay(practice.owner_snapshot)}
                  </>
                )}
                {snapshotStr(practice.owner_snapshot, "tax_code") && (
                  <>
                    <br />
                    CF: {snapshotStr(practice.owner_snapshot, "tax_code")}
                  </>
                )}
              </div>
              {/* V1 (dettaglio pratica, app.py:17170-17173): specie/razza +
                  peso + eta' ("Età: X anni") su righe separate - l'eta'
                  mancava del tutto in V2 pur essendo un dato reale gia'
                  disponibile su ogni animale (age_years/age_months, gia'
                  usati identicamente in PracticeListPage). */}
              <div className="kv">
                <small>Animale</small>
                {firstAnimal ? (
                  <>
                    {firstAnimal.species ?? "-"} - {firstAnimal.breed ?? "-"}
                    <br />
                    {firstAnimal.estimated_weight_grams ? `${firstAnimal.estimated_weight_grams / 1000} kg` : "-"}
                    <br />
                    <span className="sub">{animalAge(firstAnimal.age_years, firstAnimal.age_months)}</span>
                  </>
                ) : (
                  "-"
                )}
              </div>
              {/* V1 (dettaglio pratica, app.py:17174): "Secondo animale" e'
                  una KV IN PIU' dentro Riepilogo, non una .section "Animali"
                  separata come prima. */}
              {practice.animals.length > 1 && (
                <div className="kv">
                  <small>Secondo animale</small>
                  {practice.animals[1].name ?? "-"}
                  <br />
                  {practice.animals[1].species ?? "-"}
                  {practice.animals[1].estimated_weight_grams ? ` ${practice.animals[1].estimated_weight_grams / 1000} kg` : ""}
                </div>
              )}
              <div className="kv">
                <small>Sede</small>
                <b>{locationName || "-"}</b>
              </div>
              <div className="kv">
                <small>Origine</small>
                <b>{practice.request_origin}</b>
              </div>
              <div className="kv">
                <small>Veterinario</small>
                <b>{vetName || "-"}</b>
              </div>
              {/* V1 (app.py:17261): il template costruisce anche una cella
                  "Catalogo urna" qui, ma un .replace() finale la rimuove
                  sempre - assente dal Riepilogo realmente renderizzato. */}
            </div>
          </div>

          <div className="section">
            <h2>Note</h2>
            <p>{practice.notes || <span className="sub">Nessuna nota.</span>}</p>
          </div>

          <PracticePaymentsSection practice={currentPractice} />

          {/* V1 (dettaglio pratica, app.py:17252): sezione dedicata, non
              parte del form di modifica dati (signature_data e' protetto
              anche li'). */}
          <div className="section">
            <h2>Firma proprietario</h2>
            <p className="sub">{practice.signature_data ? "Firma salvata." : "Firma non ancora salvata."}</p>
            <div className="actions">
              <button className="btn-ghost" onClick={() => navigate(`/pratiche/${id}/firma`)}>
                Apri firma
              </button>
              {practice.signature_data && (
                <button className="btn-ghost" disabled={update.isPending} onClick={handleRemoveSignature}>
                  Rimuovi firma
                </button>
              )}
            </div>
          </div>

          <div className="section danger">
            <h2>Sposta nel Cestino</h2>
            <p className="danger-note">
              La pratica non verrà cancellata definitivamente: verrà nascosta da Dashboard e Archivio e potrai
              ripristinarla dal Cestino.
            </p>
            <button className="btn danger-btn" style={{ marginTop: 12 }} onClick={() => setTrashModalOpen(true)}>
              Sposta nel Cestino
            </button>
          </div>
        </div>

        <aside className="section">
          <h2>Cronologia modifiche</h2>
          <div className="timeline">
            {(!history || history.length === 0) && <p className="sub">Nessuna modifica registrata.</p>}
            {history?.map((entry) => {
              const change =
                entry.old_value && entry.new_value
                  ? `${entry.old_value} → ${entry.new_value}`
                  : entry.new_value || entry.old_value || "";
              return (
                <div className="event" key={entry.id}>
                  <b>{historyActionLabel(entry.action)}</b>
                  {change && (
                    <>
                      <br />
                      <span>{change}</span>
                    </>
                  )}
                  {entry.reason && (
                    <>
                      <br />
                      <span className="sub">{entry.reason}</span>
                    </>
                  )}
                  <br />
                  <small className="sub">
                    {new Date(entry.created_at).toLocaleString("it-IT")} - {entry.actor_display_name || "Sistema"}
                  </small>
                </div>
              );
            })}
          </div>
        </aside>
      </section>

      <ConfirmModal
        open={trashModalOpen}
        onClose={() => setTrashModalOpen(false)}
        onConfirm={handleTrash}
        title="Sposta nel Cestino"
        description={`Cestinare la pratica ${currentPractice.practice_number}? Non verrà cancellata definitivamente: potrai ripristinarla dal Cestino.`}
        confirmLabel="Sposta nel Cestino"
        danger
        isPending={trash.isPending}
      />
    </main>
  );
}
