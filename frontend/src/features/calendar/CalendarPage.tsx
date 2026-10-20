import { useEffect, useMemo, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";

import "./calendarPage.css";

import { ClockIcon, CheckCircleIcon, PlusIcon, ChevronRightIcon } from "@/app/icons";
import { formatMoney } from "@/shared/money";
import { useDeliveries, type Delivery } from "@/features/deliveries/api";
import { usePickups, type Pickup } from "@/features/pickups/api";
import { PICKUP_STATUS_HEX, pickupStatusStyle } from "@/shared/pickupStatusColor";
import { AdvancedSearch } from "@/shared/ui/AdvancedSearch";
import { CalendarApptCard } from "@/shared/ui/CalendarApptCard";

import {
  addDays,
  addMonths,
  isSameMonth,
  monthGridDays,
  monthRangeBounds,
  todayIso,
  weekDays,
  weekRangeBounds,
} from "./calendarDate";

const PICKUP_STATUS_LABELS: Record<string, string> = {
  da_confermare: "Da confermare",
  da_ritirare: "Da ritirare",
  ritirato: "Ritirato",
  annullato: "Annullato",
};

const DAY_NAMES = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"];
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

type ViewMode = "giorno" | "settimana" | "mese";

// Riga unificata Ritiro/Riconsegna per l'agenda - stessa gerarchia
// informativa di V1 (calendar_appointment_card): tipo, orario, titolo,
// sottotitolo, stato quando disponibile. Costruita dai dati V2 gia'
// esistenti (usePickups/useDeliveries), nessun dato inventato.
type AgendaEvent = {
  id: string;
  kind: "pickup" | "delivery";
  dayIso: string;
  startAt: string;
  title: string;
  nameLine?: string;
  ownerLine?: string;
  locationLine?: string;
  paymentText?: string | null;
  paymentPaid?: boolean;
  address?: string | null;
  status?: string;
  statusLabel?: string;
  href: string;
};

function toLocalDayIso(isoDateTime: string): string {
  const d = new Date(isoDateTime);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatTime(isoDateTime: string): string {
  return new Date(isoDateTime).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
}

function pickupToEvent(pickup: Pickup): AgendaEvent {
  const animalNames = pickup.animals.map((a) => a.name ?? "-").join(", ") || "Nessun animale";
  return {
    id: `pickup:${pickup.id}`,
    kind: "pickup",
    dayIso: toLocalDayIso(pickup.start_at),
    startAt: pickup.start_at,
    // V1 (calendar_event_display_title, app.py:9732): il titolo reale e'
    // un campo libero salvato sull'evento - V2 non lo modella ancora
    // (gap), qui il tipo di ritiro e' il dato reale piu' vicino disponibile.
    title: pickup.pickup_type,
    nameLine: `🐾 ${animalNames}`,
    ownerLine: pickup.client_name
      ? `${pickup.client_name}${pickup.client_phone ? " · " + pickup.client_phone : ""}`
      : undefined,
    locationLine: pickup.pickup_contact_name || pickup.pickup_address || undefined,
    address: pickup.pickup_address,
    status: pickup.pickup_status,
    statusLabel: PICKUP_STATUS_LABELS[pickup.pickup_status],
    href: `/ritiri/${pickup.id}`,
  };
}

function deliveryToEvent(delivery: Delivery): AgendaEvent {
  return {
    id: `delivery:${delivery.id}`,
    kind: "delivery",
    dayIso: toLocalDayIso(delivery.start_at),
    startAt: delivery.start_at,
    title: delivery.delivery_type,
    // V1 non ha un concetto di "pratica collegata" sulla card (non esiste
    // una pagina Riconsegne in V1) - riga aggiuntiva V2, utile per
    // navigare, non in conflitto con nulla di reale in V1.
    nameLine: delivery.linked_practice_id ? `Pratica #${delivery.linked_practice_id}` : "Nessuna pratica collegata",
    ownerLine: delivery.client_name
      ? `${delivery.client_name}${delivery.client_phone ? " · " + delivery.client_phone : ""}`
      : undefined,
    locationLine: delivery.delivery_address || undefined,
    // V1 (app.py:9805-9809): verde se "Pagato", giallo altrimenti.
    paymentText: delivery.preliminary_payment_status
      ? `${delivery.preliminary_payment_status}${
          delivery.preliminary_payment_amount != null ? ` · ${formatMoney(delivery.preliminary_payment_amount)}` : ""
        }`
      : null,
    paymentPaid: delivery.preliminary_payment_status === "Pagato",
    address: delivery.delivery_address,
    href: `/riconsegne/${delivery.id}`,
  };
}

// Card evento (V1: calendar_appt_card_html) - stessa card, condivisa con
// Ritiri/Riconsegne ("Le card devono essere praticamente identiche alla
// vista Giorno", richiesta esplicita in V1).
function AppointmentCard({ event }: { event: AgendaEvent }) {
  return (
    <CalendarApptCard
      href={event.href}
      kind={event.kind}
      borderColorHex={event.kind === "pickup" ? PICKUP_STATUS_HEX[event.status ?? ""] ?? "#94a3b8" : "#60a5fa"}
      timeText={formatTime(event.startAt)}
      title={event.title}
      nameLine={event.nameLine}
      ownerLine={event.ownerLine}
      locationLine={event.locationLine}
      paymentText={event.paymentText}
      paymentPaid={event.paymentPaid}
      statusText={event.statusLabel?.toUpperCase()}
      statusStyle={event.status ? pickupStatusStyle(event.status) : undefined}
      address={event.address}
    />
  );
}

function ViewSwitch({ view, onChange }: { view: ViewMode; onChange: (view: ViewMode) => void }) {
  return (
    <div className="calendar-view-switch">
      {(["giorno", "settimana", "mese"] as ViewMode[]).map((option) => (
        <button
          key={option}
          type="button"
          className={view === option ? "active" : undefined}
          onClick={() => onChange(option)}
        >
          {option === "giorno" ? "Giorno" : option === "settimana" ? "Settimana" : "Mese"}
        </button>
      ))}
    </div>
  );
}

// Vista Giorno/Settimana (V1: day_view === week_view, stesso identico
// markup - la daybar mostra sempre l'intera settimana corrente, non solo
// il giorno selezionato).
//
// Lo swipe (V1: calendarInitDayPages/calendarSelectDay, app.py:5485-5543)
// e' scroll-snap CSS nativo, non un gesto ricostruito a mano: un
// IntersectionObserver osserva ogni .calendar-day-page e, quando una
// pagina supera il 60% di visibilita', aggiorna SOLO l'evidenziazione
// della daybar/URL (mai un nuovo scrollTo da li' - "altrimenti si
// combatte con lo swipe che l'utente sta ancora facendo col dito", stesso
// commento reale di V1). Uno scrollTo esplicito parte solo da un'azione
// dell'utente che non e' gia' uno swipe (click su un chip della daybar,
// sui pulsanti di navigazione settimana, o al primo montaggio/cambio
// settimana). Le "sentinelle" prima di lunedi'/dopo domenica di V1
// (data-href che ricarica la pagina sulla settimana adiacente) sono qui
// sostituite da un cambio di settimana via stato React invece di un
// location.href pieno - stesso risultato percepito (lo swipe continua
// oltre il bordo della settimana), nessun reload della SPA.
function DaybarAgenda({
  selectedDate,
  eventsByDay,
  isLoading,
  isError,
  onSelectDate,
  kindFilter,
  onKindFilterChange,
}: {
  selectedDate: string;
  eventsByDay: Map<string, AgendaEvent[]>;
  isLoading: boolean;
  isError: boolean;
  onSelectDate: (date: string) => void;
  kindFilter: string;
  onKindFilterChange: (kind: string) => void;
}) {
  const days = weekDays(selectedDate);
  const today = todayIso();
  const pagesRef = useRef<HTMLDivElement | null>(null);
  const onSelectDateRef = useRef(onSelectDate);
  onSelectDateRef.current = onSelectDate;

  function scrollToDay(day: string, instant: boolean) {
    const container = pagesRef.current;
    // jsdom (test) non implementa Element.prototype.scrollTo - reale in
    // ogni browser, incluso Safari/iPhone, ma va comunque protetto perche'
    // qui gira dentro un effect: un'eccezione non gestita in un effect
    // interrompe React per il resto del test/render.
    if (!container || typeof container.scrollTo !== "function") return;
    const page = container.querySelector<HTMLElement>(`[data-day-iso="${day}"]`);
    if (page) container.scrollTo({ left: page.offsetLeft, behavior: instant ? "auto" : "smooth" });
  }

  // Cambio settimana (nav ‹/› o al montaggio): riposiziona SUBITO sul
  // giorno selezionato, senza animazione (V1: calendarSelectDay(idx,
  // {instant:true}) nell'init di calendarInitDayPages). Dipende anche da
  // isLoading: il contenitore .calendar-day-pages e' montato solo a
  // caricamento finito (sotto), quindi un cambio settimana che parte con
  // dati non ancora in cache deve rieseguire l'effetto quando il
  // contenitore compare davvero, non solo quando cambia la settimana.
  const weekKey = days.join(",");
  useEffect(() => {
    if (isLoading) return;
    scrollToDay(selectedDate, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [weekKey, isLoading]);

  useEffect(() => {
    if (isLoading) return;
    const container = pagesRef.current;
    if (!container || !("IntersectionObserver" in window)) return;
    const items = [...container.querySelectorAll<HTMLElement>("[data-day-iso]")];
    if (!items.length) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting || entry.intersectionRatio < 0.6) continue;
          const day = entry.target.getAttribute("data-day-iso");
          if (day) onSelectDateRef.current(day);
        }
      },
      { root: container, threshold: [0.6] },
    );
    items.forEach((item) => observer.observe(item));
    return () => observer.disconnect();
  }, [weekKey, isLoading]);

  function selectFromDaybar(day: string) {
    onSelectDate(day);
    scrollToDay(day, false);
  }

  return (
    <div>
      <div className="calendar-daybar-wrap">
        <button type="button" className="calendar-daybar-nav" onClick={() => onSelectDate(addDays(selectedDate, -7))} aria-label="Settimana precedente">
          ‹
        </button>
        <div className="calendar-daybar">
          {days.map((day) => {
            const count = eventsByDay.get(day)?.length ?? 0;
            const dow = DAY_NAMES[(new Date(`${day}T00:00:00`).getDay() + 6) % 7];
            return (
              <button
                key={day}
                type="button"
                className={`calendar-daybar-card${day === selectedDate ? " active" : ""}${day === today ? " is-today" : ""}`}
                onClick={() => selectFromDaybar(day)}
              >
                <span className="calendar-daybar-dow">{dow.toUpperCase()}</span>
                <span className="calendar-daybar-num">{Number(day.slice(8, 10))}</span>
                <span className="calendar-daybar-count">{count} app.</span>
              </button>
            );
          })}
        </div>
        <button type="button" className="calendar-daybar-nav" onClick={() => onSelectDate(addDays(selectedDate, 7))} aria-label="Settimana successiva">
          ›
        </button>
      </div>

      {/* V1 (app.py:10075, calendar-route-edge-row/.route-fab-edge + route-sheet):
          il pulsante "Percorso giornaliero" apre un pannello di pianificazione
          tappe con geolocalizzazione - V2 non ha alcun backend di calcolo
          percorso (nessuna route /percorso-giornaliero), gap disclosed, non
          fabbricato qui. */}

      {/* V1 (app.py:10028-10033, filter_pills_html): pill di filtro rapido
          Tutti/Ritiri/Riconsegne, condivise con lo stesso stato "tipo" gia'
          usato dal filtro Tipo dei filtri avanzati sotto - stessa fonte di
          verita', solo un accesso rapido in piu' come in V1. Il filtro
          "Incaricato" di V1 non e' replicato: richiede un operatore
          assegnato per ritiro/riconsegna, dato che V2 non modella (gap).*/}
      <div className="calendar-appt-filters">
        <button
          type="button"
          className={`calendar-filter-pill${kindFilter === "" ? " active" : ""}`}
          onClick={() => onKindFilterChange("")}
        >
          Tutti
        </button>
        <button
          type="button"
          className={`calendar-filter-pill${kindFilter === "pickup" ? " active" : ""}`}
          onClick={() => onKindFilterChange("pickup")}
        >
          Ritiri
        </button>
        <button
          type="button"
          className={`calendar-filter-pill${kindFilter === "delivery" ? " active" : ""}`}
          onClick={() => onKindFilterChange("delivery")}
        >
          Riconsegne
        </button>
      </div>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento del calendario.</p>}
      {!isLoading && !isError && (
        <div className="calendar-day-pages" ref={pagesRef}>
          {days.map((day) => {
            const dayEvents = [...(eventsByDay.get(day) ?? [])].sort((a, b) => a.startAt.localeCompare(b.startAt));
            const pendingCount = dayEvents.filter((e) => e.kind === "pickup" && e.status !== "ritirato").length;
            const doneCount = dayEvents.filter((e) => e.kind === "pickup" && e.status === "ritirato").length;
            return (
              <div className="calendar-day-page" key={day} data-day-iso={day}>
                <div className="calendar-appt-stats calendar-appt-stats-2col">
                  <div className="calendar-appt-stat calendar-appt-stat-pending">
                    <span className="calendar-appt-stat-icon">
                      <ClockIcon />
                    </span>
                    <b>{pendingCount}</b>
                    <small>Da effettuare</small>
                  </div>
                  <div className="calendar-appt-stat calendar-appt-stat-done">
                    <span className="calendar-appt-stat-icon">
                      <CheckCircleIcon />
                    </span>
                    <b>{doneCount}</b>
                    <small>Completati</small>
                  </div>
                </div>

                <div className="calendar-appt-list">
                  {dayEvents.length === 0 ? (
                    <p className="calendar-appt-empty">Nessun appuntamento in programma.</p>
                  ) : (
                    dayEvents.map((event) => <AppointmentCard key={event.id} event={event} />)
                  )}
                </div>

                <Link className="calendar-add-appt-btn" to={`/ritiri/nuovo?data=${day}`}>
                  <PlusIcon />
                  <span>Aggiungi ritiro / riconsegna</span>
                </Link>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Vista Mese (V1: calendar-month-v2 - griglia 42 celle con pallini
// conteggio Ritiri/Riconsegne, cella selezionata mostra un riepilogo
// sotto la griglia invece di aprire subito il dettaglio giorno).
function MonthGrid({
  selectedDate,
  eventsByDay,
  onSelectDate,
  onOpenDay,
}: {
  selectedDate: string;
  eventsByDay: Map<string, AgendaEvent[]>;
  onSelectDate: (date: string) => void;
  onOpenDay: (date: string) => void;
}) {
  const gridDays = monthGridDays(selectedDate);
  const today = todayIso();
  const selectedEvents = eventsByDay.get(selectedDate) ?? [];
  const selectedPickups = selectedEvents.filter((e) => e.kind === "pickup").length;
  const selectedDeliveries = selectedEvents.filter((e) => e.kind === "delivery").length;
  const selectedDone = selectedEvents.filter((e) => e.kind === "pickup" && e.status === "ritirato").length;
  const selectedDateObj = new Date(`${selectedDate}T00:00:00`);
  const selectedDateLabel = `${DAY_NAMES[(selectedDateObj.getDay() + 6) % 7]} ${selectedDateObj.getDate()} ${MONTH_NAMES[selectedDateObj.getMonth()]}`;

  return (
    <div className="calendar-month-composition">
      <section className="calendar-month-v2">
        <div className="calendar-month-v2-dow-row">
          {DAY_NAMES.map((name) => (
            <div className="calendar-month-v2-dow" key={name}>
              {name}
            </div>
          ))}
        </div>
        <div className="calendar-month-v2-grid">
          {gridDays.map((day) => {
            const events = eventsByDay.get(day) ?? [];
            const pickups = events.filter((e) => e.kind === "pickup").length;
            const deliveries = events.filter((e) => e.kind === "delivery").length;
            const classes = ["calendar-month-v2-cell"];
            if (day === today) classes.push("is-today");
            if (!isSameMonth(day, selectedDate)) classes.push("is-other-month");
            if (day === selectedDate) classes.push("selected");
            return (
              <button key={day} type="button" className={classes.join(" ")} onClick={() => onSelectDate(day)}>
                <span className="calendar-month-v2-num">{Number(day.slice(8, 10))}</span>
                <div className="calendar-month-v2-counts">
                  {pickups > 0 && <span className="calendar-month-v2-count calendar-month-v2-count-pickup">{pickups}</span>}
                  {deliveries > 0 && <span className="calendar-month-v2-count calendar-month-v2-count-delivery">{deliveries}</span>}
                </div>
              </button>
            );
          })}
        </div>
        <div className="calendar-month-v2-legend">
          <span>
            <span className="calendar-month-v2-count calendar-month-v2-count-pickup"></span>Ritiri
          </span>
          <span>
            <span className="calendar-month-v2-count calendar-month-v2-count-delivery"></span>Riconsegne
          </span>
        </div>
      </section>

      <section className="calendar-month-daycard">
        <header>
          <h2>{selectedDateLabel}</h2>
          <span className="badge">{selectedEvents.length} appuntamenti</span>
        </header>
        <div className="calendar-appt-stats">
          <div className="calendar-appt-stat">
            <b>{selectedPickups}</b>
            <small>Ritiri</small>
          </div>
          <div className="calendar-appt-stat">
            <b>{selectedDeliveries}</b>
            <small>Riconsegne</small>
          </div>
          <div className="calendar-appt-stat">
            <b>{selectedDone}</b>
            <small>Completati</small>
          </div>
        </div>
        <div className="calendar-month-daycard-actions">
          <Link className="btn-ghost calendar-month-new-btn" to={`/ritiri/nuovo?data=${selectedDate}`}>
            <PlusIcon />
            <span>Nuovo evento</span>
          </Link>
          <button type="button" className="btn calendar-month-detail-btn" onClick={() => onOpenDay(selectedDate)}>
            <span>Vai al dettaglio del giorno</span>
            <ChevronRightIcon />
          </button>
        </div>
      </section>
    </div>
  );
}

// V1 (filters_html): "Tipo" e "Stato" sono gli unici due filtri qui
// replicati con dati V2 reali gia' disponibili (event_type/pickup_status)
// e applicati client-side ai risultati gia' scaricati per la settimana/
// mese corrente - nessuna nuova chiamata di rete. Operatore/Veterinario/
// Zona/Sede di V1 non sono replicati: vedi report (richiederebbero
// parametri di filtro aggiuntivi sugli endpoint ritiri/riconsegne, non
// presenti oggi, o dati come l'operatore che V2 non ha affatto).
function filterEvents(eventsByDay: Map<string, AgendaEvent[]>, kind: string, status: string): Map<string, AgendaEvent[]> {
  if (!kind && !status) return eventsByDay;
  const filtered = new Map<string, AgendaEvent[]>();
  for (const [day, events] of eventsByDay) {
    const list = events.filter((e) => (!kind || e.kind === kind) && (!status || e.status === status));
    if (list.length > 0) filtered.set(day, list);
  }
  return filtered;
}

export function CalendarPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const date = searchParams.get("data") || todayIso();
  const view = ((searchParams.get("vista") as ViewMode) || "giorno") as ViewMode;
  const kindFilter = searchParams.get("tipo") ?? "";
  const statusFilter = searchParams.get("stato") ?? "";

  // V1 (app.py:9931-9932, back_view/"da="): quando si apre il dettaglio di
  // un giorno dal riepilogo della vista Mese, un pulsante "Torna a Mese"
  // compare in cima alla vista Giorno - qui lo stesso, tramite il parametro
  // "da" nell'URL invece che una var lato server.
  const backView = searchParams.get("da") === "mese" ? "mese" : "";

  function setParams(next: { data?: string; vista?: ViewMode; tipo?: string; stato?: string; da?: string }) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next.data) params.set("data", next.data);
        if (next.vista) params.set("vista", next.vista);
        if (next.tipo !== undefined) {
          if (next.tipo) params.set("tipo", next.tipo);
          else params.delete("tipo");
        }
        if (next.stato !== undefined) {
          if (next.stato) params.set("stato", next.stato);
          else params.delete("stato");
        }
        if (next.da !== undefined) {
          if (next.da) params.set("da", next.da);
          else params.delete("da");
        }
        return params;
      },
      // replace, non push: cambiare giorno/vista/filtro e' una regolazione
      // del contesto corrente del Calendario, non una nuova destinazione.
      { replace: true },
    );
  }

  const isMonth = view === "mese";
  const { dateFrom, dateTo } = isMonth ? monthRangeBounds(date) : weekRangeBounds(date);
  const { data: pickups, isLoading: pickupsLoading, isError: pickupsError } = usePickups({ dateFrom, dateTo });
  const { data: deliveries, isLoading: deliveriesLoading, isError: deliveriesError } = useDeliveries({ dateFrom, dateTo });

  const eventsByDay = useMemo(() => {
    const map = new Map<string, AgendaEvent[]>();
    for (const pickup of pickups ?? []) {
      const event = pickupToEvent(pickup);
      const list = map.get(event.dayIso) ?? [];
      list.push(event);
      map.set(event.dayIso, list);
    }
    for (const delivery of deliveries ?? []) {
      const event = deliveryToEvent(delivery);
      const list = map.get(event.dayIso) ?? [];
      list.push(event);
      map.set(event.dayIso, list);
    }
    return filterEvents(map, kindFilter, statusFilter);
  }, [pickups, deliveries, kindFilter, statusFilter]);

  const selectedDateObj = new Date(`${date}T00:00:00`);
  const dateTitle =
    view === "mese"
      ? `${MONTH_NAMES[selectedDateObj.getMonth()]} ${selectedDateObj.getFullYear()}`
      : `${selectedDateObj.getDate()} ${MONTH_NAMES[selectedDateObj.getMonth()]} ${selectedDateObj.getFullYear()}`;

  return (
    <main className="wrap calendar-wrap">
      {/* V1 (app.py:10171, titlebar.calendar-main-title): titolo fisso +
          sottotitolo descrittivo fisso, non piu' la data selezionata (quella
          vive nel calendar-date-nav qui sotto, come in V1). */}
      <div className="titlebar calendar-main-title">
        <div>
          {view === "giorno" && backView === "mese" && (
            <Link className="btn-ghost calendar-back-btn" to={`/calendario?vista=mese&data=${date}`}>
              ← Torna a Mese
            </Link>
          )}
          <h1>Calendario operativo</h1>
          <p className="sub">Ritiri, riconsegne e promemoria</p>
        </div>
        {/* V1 (app.py:10171, .calendar-quick-actions): Cestino/Impostazioni
            del Calendario - V2 non ha ne' un cestino ne' una pagina
            impostazioni unificati per il Calendario (gap, non fabbricato). */}
      </div>

      <nav className="calendar-date-nav">
        <button
          type="button"
          className="btn ghost"
          aria-label={view === "mese" ? "Mese precedente" : "Giorno precedente"}
          onClick={() => setParams({ data: view === "mese" ? addMonths(date, -1) : addDays(date, -1) })}
        >
          ←
        </button>
        <label className="calendar-date-title">
          <span>{dateTitle}</span>
          <input type="date" value={date} onChange={(e) => setParams({ data: e.target.value })} />
        </label>
        <button
          type="button"
          className="btn ghost"
          aria-label={view === "mese" ? "Mese successivo" : "Giorno successivo"}
          onClick={() => setParams({ data: view === "mese" ? addMonths(date, 1) : addDays(date, 1) })}
        >
          →
        </button>
        <button type="button" className="btn ghost calendar-today" aria-label="Oggi" onClick={() => setParams({ data: todayIso() })}>
          OGGI
        </button>
      </nav>

      <div className="calendar-toolbar">
        <ViewSwitch view={view} onChange={(next) => setParams({ vista: next })} />
      </div>

      {view === "mese" ? (
        <MonthGrid
          selectedDate={date}
          eventsByDay={eventsByDay}
          onSelectDate={(next) => setParams({ data: next })}
          onOpenDay={(next) => setParams({ data: next, vista: "giorno", da: "mese" })}
        />
      ) : (
        <DaybarAgenda
          selectedDate={date}
          eventsByDay={eventsByDay}
          isLoading={pickupsLoading || deliveriesLoading}
          isError={pickupsError || deliveriesError}
          onSelectDate={(next) => setParams({ data: next })}
          kindFilter={kindFilter}
          onKindFilterChange={(kind) => setParams({ tipo: kind })}
        />
      )}

      {/* V1 (app.py:10171, "{content}{filters_html}"): la ricerca avanzata
          vive sotto la vista corrente, non sopra - stesso ordine qui. */}
      <AdvancedSearch defaultOpen={Boolean(kindFilter || statusFilter)}>
        <div className="field-row">
          <select value={kindFilter} onChange={(e) => setParams({ tipo: e.target.value })}>
            <option value="">Tutti i tipi</option>
            <option value="pickup">Ritiro</option>
            <option value="delivery">Riconsegna</option>
          </select>
          <select value={statusFilter} onChange={(e) => setParams({ stato: e.target.value })}>
            <option value="">Tutti gli stati dei ritiri</option>
            {Object.entries(PICKUP_STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </AdvancedSearch>
    </main>
  );
}
