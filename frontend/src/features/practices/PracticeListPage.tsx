import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { usePracticesPaymentsSummary, type PracticePaymentsSummary } from "@/features/payments/api";
import { AdvancedSearch } from "@/shared/ui/AdvancedSearch";
import { formatMoney } from "@/shared/money";
import { speciesAvatar } from "@/shared/speciesAvatar";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useCompanyLocations, usePractices, useTags, type Practice } from "./api";
import { useVeterinarians } from "@/features/veterinarians/api";

const STATUS_LABELS: Record<string, string> = {
  ritirato: "Ritirato",
  in_programma: "In programma",
  cremato: "Cremato",
  da_consegnare: "Da consegnare",
  consegnato: "Consegnato",
  smaltito: "Smaltito",
};

const MONTH_NAMES = [
  "Gennaio",
  "Febbraio",
  "Marzo",
  "Aprile",
  "Maggio",
  "Giugno",
  "Luglio",
  "Agosto",
  "Settembre",
  "Ottobre",
  "Novembre",
  "Dicembre",
];

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


// V1 (status_badges): la cella "Stati" mostra DUE badge affiancati - stato
// pratica + stato pagamento (Da saldare/Acconto/Pagato, pay-yellow/pay-
// blue/pay-green). In V1 il badge pagamento apre anche un popover di
// modifica rapida (payment_movements, il motore pagamenti VECCHIO che V2
// sostituisce) - qui riportato come badge statico, la modifica avviene
// nella sezione Pagamenti del Dettaglio Pratica (motore V2 invariato).
function paymentBadge(dueCents: number, paidCents: number): { label: string; cls: string } {
  if (paidCents <= 0) return { label: "Da saldare", cls: "pay-yellow" };
  if (paidCents < dueCents) return { label: "Acconto", cls: "pay-blue" };
  return { label: "Pagato", cls: "pay-green" };
}

function ageLabel(years?: number | null, months?: number | null): string {
  const parts: string[] = [];
  if (years) parts.push(`${years} anni`);
  if (months) parts.push(`${months} mesi`);
  return parts.join(" e ") || "-";
}

function monthKey(dateIso: string): string {
  return dateIso.slice(0, 7);
}

function monthLabel(key: string): string {
  const [year, month] = key.split("-");
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

// V1 (elenco Pratiche, practice_rows): una riga per pratica con la stessa
// gerarchia di colonne - Animale/Eta'/Proprietario/Data/Codice/
// Veterinario/Origine/Sede/Etichetta/Note/Totale pagato/Fattura/Stato/
// Azione. Veterinario e Sede sono risolti da id a nome tramite gli
// endpoint di riferimento gia' esistenti (useVeterinarians,
// useCompanyLocations); Totale pagato/Fattura tramite
// usePracticesPaymentsSummary (bulk, 2 query per l'intera pagina - vedi
// payment_service.get_practices_payments_summary). "Urna" resta fuori:
// verificato che line_items.urn_catalog_id ESISTE come FK reale
// (models/practice.py:261), ma nessun flusso V2 scrive ancora una voce
// category="urna" su una pratica (PracticeFormPage non gestisce
// line_items, e la modifica pratica li esclude deliberatamente per non
// toccare la zona economica - vedi practiceUpdateMapper.ts) - la colonna
// mostrerebbe sempre "-" per qualunque pratica creata da V2, quindi non
// e' un vero gap di sola presentazione: resta un gap di dominio (manca
// il flusso di selezione urna in creazione/modifica pratica).
function PracticeRow({
  practice,
  vetName,
  locationName,
  tagLabels,
  summary,
}: {
  practice: Practice;
  vetName: string;
  locationName: string;
  tagLabels: string[];
  summary: PracticePaymentsSummary | undefined;
}) {
  const firstAnimal = practice.animals[0];
  const owner = ownerName(practice.owner_snapshot);
  const phone = ownerPhone(practice.owner_snapshot);
  const practiceUrl = `/pratiche/${practice.id}`;
  const createdDate = new Date(practice.created_at).toLocaleDateString("it-IT");
  const [avatarEmoji, avatarCls] = speciesAvatar(firstAnimal?.species);

  return (
    <tr className={avatarCls}>
      <td>
        <div className="practice-row-animal">
          <span className={`practice-row-avatar ${avatarCls}`} aria-hidden="true">
            {avatarEmoji}
          </span>
          <div className="practice-row-animal-copy">
            {firstAnimal?.name ?? "Da inserire"}
            <br />
            <small className="sub">
              {firstAnimal?.species ?? "-"}
              {firstAnimal?.estimated_weight_grams ? ` - ${firstAnimal.estimated_weight_grams / 1000} kg` : ""}
            </small>
          </div>
        </div>
      </td>
      <td>{ageLabel(firstAnimal?.age_years, firstAnimal?.age_months)}</td>
      <td>
        {owner || "-"}
        {phone && (
          <>
            <br />
            <small className="sub">{phone}</small>
          </>
        )}
      </td>
      <td>{createdDate}</td>
      <td>
        <Link to={practiceUrl}>
          <b>{practice.practice_number}</b>
        </Link>
      </td>
      <td>{vetName || "-"}</td>
      <td>
        <b>{practice.request_origin}</b>
      </td>
      <td>{locationName || "-"}</td>
      <td>
        {tagLabels.length > 0 ? (
          tagLabels.map((label) => (
            <span className="badge" key={label} style={{ marginRight: 4 }}>
              {label}
            </span>
          ))
        ) : (
          <span className="sub">-</span>
        )}
      </td>
      <td>{practice.notes ? practice.notes.slice(0, 70) + (practice.notes.length > 70 ? "..." : "") : <span className="sub">-</span>}</td>
      <td>{summary ? formatMoney(summary.paid_total_cents) : "-"}</td>
      <td>
        {summary && summary.invoices.length > 0 ? (
          summary.invoices.map((invoice) => (
            <div key={invoice.invoice_number}>
              {invoice.invoice_number}
              <br />
              <small className="sub">{formatMoney(invoice.total_amount_cents)}</small>
            </div>
          ))
        ) : (
          <span className="sub">-</span>
        )}
      </td>
      <td>{formatMoney(practice.effective_total_cents)}</td>
      {/* V1 (Totale D, total_text): un secondo totale manuale distinto,
          inserito a mano solo per le pratiche a circuito D - V2 ha un solo
          totale (effective_total_cents), nessun campo separato equivalente.
          Nessun dato inventato: sempre "-", come mostra V1 stesso quando
          total_text e' vuoto (la maggioranza delle pratiche W). */}
      <td>
        <span className="sub">-</span>
      </td>
      <td>{summary ? formatMoney(summary.acconto_total_cents) : "-"}</td>
      <td>{summary ? formatMoney(Math.max(0, practice.effective_total_cents - summary.paid_total_cents)) : "-"}</td>
      <td>
        <div className="inline-statuses">
          <span className={`badge practice-status-${practice.status}`}>{STATUS_LABELS[practice.status]}</span>
          {(() => {
            const pay = paymentBadge(practice.effective_total_cents, summary?.paid_total_cents ?? 0);
            return <span className={`badge ${pay.cls}`}>{pay.label}</span>;
          })()}
        </div>
      </td>
      <td>
        <Link className="btn-ghost" to={practiceUrl}>
          Apri
        </Link>
      </td>
    </tr>
  );
}

function MonthSection({
  monthKeyValue,
  practices,
  vetNameById,
  locationNameById,
  tagLabelById,
  summaryByPracticeId,
  collapsed,
  onToggle,
}: {
  monthKeyValue: string;
  practices: Practice[];
  vetNameById: Map<number, string>;
  locationNameById: Map<number, string>;
  tagLabelById: Map<number, string>;
  summaryByPracticeId: Map<number, PracticePaymentsSummary>;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <section className="month-block">
      <div className="month-title" onClick={onToggle}>
        <div className="month-heading">
          {/* V1 (toggleArchiveMonth, app.py:15231): il simbolo del toggle e'
              testo "+"/"-" (il CSS .month-toggle e' tarato su quel glifo:
              font-size 22px, line-height 1), non una freccia unicode. */}
          <button type="button" className="month-toggle" aria-expanded={!collapsed}>
            {collapsed ? "+" : "-"}
          </button>
          <h2>{monthLabel(monthKeyValue)}</h2>
        </div>
        <span className="badge">{practices.length} pratiche</span>
      </div>
      {/* V1 (archive(), app.py:15187): il vero wrapper e' `.tablebox
          .dashboard-table-scroll .archive-tablebox`, e la tabella ha SOLO
          la classe `.practice-list-table` (niente `.data-table` generico:
          quella classe forza `display:block`/`white-space:nowrap` sulla
          tabella stessa, cosa che V1 non fa - lo scroll orizzontale reale
          e' delegato al div contenitore). `.practice-list-table-scroll`
          e' gia' presente in styles.css con lo stesso overflow-x:auto -
          wrapper equivalente, non toccato qui (styles.css e' di altri
          agenti in parallelo). */}
      {!collapsed && (
        <div className="practice-list-table-scroll">
          <table className="practice-list-table">
            <thead>
              <tr>
                <th>Animale</th>
                <th>Età</th>
                <th>Proprietario</th>
                <th>Data recupero</th>
                <th>Codice pratica</th>
                <th>Veterinario</th>
                <th>Provenienza</th>
                <th>Sede</th>
                <th>Etichetta</th>
                <th>Note</th>
                <th>Totale pagato</th>
                <th>Fattura</th>
                <th>Totale W</th>
                <th>Totale D</th>
                <th>Acconto</th>
                <th>Rimanenza</th>
                <th>Stati</th>
                <th>Azione</th>
              </tr>
            </thead>
            <tbody>
              {practices.map((practice) => (
                <PracticeRow
                  key={practice.id}
                  practice={practice}
                  vetName={practice.veterinarian_id ? (vetNameById.get(practice.veterinarian_id) ?? "") : ""}
                  locationName={locationNameById.get(practice.destination_branch_id) ?? ""}
                  tagLabels={(practice.tags ?? []).map((id) => tagLabelById.get(id)).filter((x): x is string => Boolean(x))}
                  summary={summaryByPracticeId.get(practice.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function PracticeListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get("status") ?? "";
  const [collapsedMonths, setCollapsedMonths] = useState<Set<string>>(new Set());

  function setStatus(value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("status", value);
        else next.delete("status");
        next.delete("offset");
        return next;
      },
      { replace: true },
    );
  }

  const { data: practices, isLoading, isError } = usePractices({ q, status: status || undefined, offset });
  const { data: veterinarians } = useVeterinarians({});
  const { data: locations } = useCompanyLocations();
  const { data: tags } = useTags();
  const practiceIds = useMemo(() => (practices ?? []).map((p) => p.id), [practices]);
  const { data: paymentsSummary } = usePracticesPaymentsSummary(practiceIds);

  const summaryByPracticeId = useMemo(() => {
    const map = new Map<number, PracticePaymentsSummary>();
    for (const summary of paymentsSummary ?? []) map.set(summary.practice_id, summary);
    return map;
  }, [paymentsSummary]);

  const vetNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const vet of veterinarians ?? []) map.set(vet.id, vet.short_name || vet.clinic_name || vet.doctor_name || "");
    return map;
  }, [veterinarians]);
  const locationNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const location of locations ?? []) map.set(location.id, location.name);
    return map;
  }, [locations]);
  const tagLabelById = useMemo(() => {
    const map = new Map<number, string>();
    for (const tag of tags ?? []) map.set(tag.id, tag.label);
    return map;
  }, [tags]);

  // V1 (archivio Pratiche): elenco raggruppato per mese, sezioni
  // collassabili - qui applicato alla pagina corrente di risultati (V2
  // pagina l'elenco 50 alla volta; V1 non pagina l'archivio). Ordine
  // gia' piu' recente prima (stesso ordine restituito dall'API).
  const monthGroups = useMemo(() => {
    const groups = new Map<string, Practice[]>();
    for (const practice of practices ?? []) {
      const key = monthKey(practice.created_at);
      const list = groups.get(key) ?? [];
      list.push(practice);
      groups.set(key, list);
    }
    return [...groups.entries()].sort((a, b) => b[0].localeCompare(a[0]));
  }, [practices]);

  function toggleMonth(key: string) {
    setCollapsedMonths((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <main className="wrap">
      <div className="titlebar">
        <div>
          <h1>ARCHIVIO</h1>
          <div className="sub">{practices ? `${practices.length} pratiche trovate` : ""}</div>
        </div>
        <div className="actions">
          {/* V1 (archivio, app.py:14843): il Cestino e' raggiungibile
              dall'hub Archivio - V2 non replica l'hub, questo e' il punto
              di accesso piu' vicino e naturale (stessa sezione Pratiche). */}
          <Link className="btn-ghost" to="/cestino">
            Cestino
          </Link>
          <Link className="btn" to="/pratiche/nuova">
            + Nuova pratica
          </Link>
        </div>
      </div>

      {/* V1 (collapse_advanced_search): un form con 2+ controlli (qui
          ricerca testuale + select stato) viene automaticamente collassato
          in "Ricerca avanzata" - un solo campo di ricerca resta invece
          sempre visibile (vedi le altre liste con un solo filtro). */}
      <AdvancedSearch defaultOpen={Boolean(q || status)}>
        <input
          className="search-input"
          placeholder="Cerca per numero pratica o cliente..."
          defaultValue={q}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Tutti gli stati</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </AdvancedSearch>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento delle pratiche.</p>}
      {practices && practices.length === 0 && <p className="empty-state">Nessuna pratica trovata.</p>}

      {monthGroups.map(([key, monthPractices]) => (
        <MonthSection
          key={key}
          monthKeyValue={key}
          practices={monthPractices}
          vetNameById={vetNameById}
          locationNameById={locationNameById}
          tagLabelById={tagLabelById}
          summaryByPracticeId={summaryByPracticeId}
          collapsed={collapsedMonths.has(key)}
          onToggle={() => toggleMonth(key)}
        />
      ))}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!practices || practices.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
      {/* V1 raggruppa per mese l'intero archivio (non paginato). V2 pagina
          l'elenco 50 alla volta: raggruppare l'intero archivio scaricando
          tutte le pratiche peggiorerebbe le prestazioni, quindi il
          raggruppamento qui riflette solo la pagina corrente - un mese può
          continuare nella pagina successiva. */}
      {practices && practices.length > 0 && (
        <p className="sub" style={{ marginTop: 8 }}>
          Il raggruppamento per mese riflette solo i risultati della pagina corrente.
        </p>
      )}
    </main>
  );
}
