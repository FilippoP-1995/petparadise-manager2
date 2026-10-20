import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { addDays, todayIso } from "@/features/calendar/calendarDate";
import { CalendarIcon, CheckCircleIcon, ChevronRightIcon, ClockIcon, FlameIcon, PawIcon, PlusIcon } from "@/app/icons";
import { speciesAvatar } from "@/shared/speciesAvatar";
import { CREMATION_STATUS } from "@/shared/cremationStatus";

import {
  useAssignAnimal,
  useCompleteCycle,
  useCremationCycles,
  useCreateCremationCycle,
  useEligibleAnimals,
  useRevertCycle,
  type CremationCycle,
  type CycleAnimal,
} from "./api";

import "./cremationCycles.css";

const MONTH_NAMES_IT = [
  "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
  "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
];
const WEEKDAY_SHORT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"];

// V1 (CREMATION_DAY_START/CREMATION_CYCLE_DURATION_MIN): un nuovo ciclo
// creato al volo dal menu rapido parte da qui - stesso punto di partenza
// ragionevole, l'orario resta comunque modificabile dal dettaglio ciclo.
const DAY_START = "09:00";
const CYCLE_DURATION_MIN = 45;

function addMinutes(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  const total = h * 60 + m + minutes;
  const hh = Math.floor(total / 60) % 24;
  const mm = total % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

// V1 (cremation_schedule, app.py:11964-12281): board giornaliera reale -
// non l'elenco piatto di tutti i cicli di prima. Sezione "In attesa"
// (animali idonei non ancora in un ciclo, gia' reali via
// useEligibleAnimals - nessun dominio nuovo) + timeline dei cicli del
// giorno selezionato. NOTA SUI DATI: CycleAnimalRead espone solo
// id/name/species/practice_id - peso/etichette/urna/note/provenienza
// (reali in V1) non sono modellati su questo read model V2, quindi non
// vengono mostrati qui (nessun dato inventato). Drag-and-drop, scambio
// animali tra cicli, badge "avvisato/da avvisare" e vista Settimana
// restano fuori (vedi report finale) - il click su una card ciclo porta
// al dettaglio reale (/cicli-cremazione/:id), dove assegnazione/rimozione/
// completamento/ripristino sono gia' realmente funzionanti e testati.
export function CremationCycleListPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const selectedDate = searchParams.get("data") || todayIso();
  const today = todayIso();

  const { data: cycles, isLoading, isError } = useCremationCycles({ cycleDate: selectedDate });
  const { data: waiting } = useEligibleAnimals();
  const assignAnimal = useAssignAnimal();
  const createCycle = useCreateCremationCycle();
  const completeCycle = useCompleteCycle();
  const revertCycle = useRevertCycle();
  const [openMenuFor, setOpenMenuFor] = useState<number | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // V1 (cremationToggleWaitingPanel, cremation-waiting-panel): la lista
  // "In attesa" e' un pannello che si apre/chiude cliccando la stat card
  // corrispondente - non una .section sempre visibile.
  const [waitingOpen, setWaitingOpen] = useState(false);

  function goToDate(date: string) {
    navigate(`/cicli-cremazione?data=${date}`);
  }

  const dateObj = new Date(`${selectedDate}T00:00:00`);
  const dateLabel = `${WEEKDAY_SHORT[(dateObj.getDay() + 6) % 7]} ${dateObj.getDate()} ${MONTH_NAMES_IT[dateObj.getMonth()]} ${dateObj.getFullYear()}`;

  const totalCycles = cycles?.length ?? 0;
  const completedCount = cycles?.filter((c) => c.status === "completato").length ?? 0;
  const animaliCount = cycles?.reduce((sum, c) => sum + c.animals.length, 0) ?? 0;
  // V1 (fine_prevista/current_index/progress_pct, app.py:12180/12249-12250):
  // stessi identici calcoli - il piu' tardo planned_end tra i cicli del
  // giorno, e l'indice del ciclo corrente = completati+1.
  const finePrevista = (cycles ?? []).reduce((max, c) => (c.planned_end > max ? c.planned_end : max), "");
  const currentIndex = totalCycles ? Math.min(completedCount + 1, totalCycles) : 0;
  const dayStart = cycles && cycles.length > 0 ? cycles[0].planned_start.slice(0, 5) : DAY_START;
  const dayEnd = finePrevista ? finePrevista.slice(0, 5) : addMinutes(dayStart, CYCLE_DURATION_MIN);

  // V1 (insertable_cycles, app.py:12036): cicli non completati con meno
  // di 2 animali del giorno selezionato - stesso criterio esatto.
  const insertableCycles = (cycles ?? []).filter((c) => c.status !== "completato" && c.animals.length < 2);

  async function handleQuickInsert(animalId: number, cycleId: number) {
    setActionError(null);
    setOpenMenuFor(null);
    try {
      await assignAnimal.mutateAsync({ cycleId, animalId });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Assegnazione non riuscita");
    }
  }

  async function handleQuickCreateAndAssign(animalId: number) {
    setActionError(null);
    setOpenMenuFor(null);
    try {
      const plannedStart = cycles && cycles.length > 0 ? cycles[cycles.length - 1].planned_end : DAY_START;
      const newCycle = await createCycle.mutateAsync({
        cycle_date: selectedDate,
        planned_start: plannedStart,
        planned_end: addMinutes(plannedStart, CYCLE_DURATION_MIN),
      });
      await assignAnimal.mutateAsync({ cycleId: newCycle.id, animalId });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Creazione ciclo non riuscita");
    }
  }

  async function handleComplete(cycleId: number) {
    setActionError(null);
    try {
      await completeCycle.mutateAsync({ cycleId });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Completamento non riuscito");
    }
  }

  async function handleRevert(cycleId: number) {
    setActionError(null);
    try {
      await revertCycle.mutateAsync({ cycleId, reason: "Ripristino da board giornaliera" });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Ripristino non riuscito");
    }
  }

  return (
    // V1 (cremation_schedule, app.py:12396-12421): <main class="wrap
    // cremation-board"><div class="cremation-header"> con h1+sub+date-nav,
    // non un .titlebar con un link "+ Nuovo ciclo" a destra (V1 non ce
    // l'ha li' - "Aggiungi nuovo ciclo" e' un bottone in fondo alla
    // colonna cicli, vedi sotto). La lista "In attesa" e' un pannello
    // aperto/chiuso dal click sulla stat card corrispondente
    // (cremationToggleWaitingPanel), non una .section sempre visibile.
    <main className="wrap">
      <div className="cremation-header">
        <div className="cremation-header-title">
          <h1>
            Programma cremazioni <CalendarIcon className="cremation-title-icon" />
          </h1>
          <p className="sub">Pianifica e monitora i cicli di cremazione della giornata</p>
        </div>
        <div className="cremation-header-nav">
          <div className="cremation-date-nav">
            <button type="button" className="cremation-nav-btn" onClick={() => goToDate(addDays(selectedDate, -1))} aria-label="Giorno precedente">
              ‹
            </button>
            <div className="cremation-date-label">
              <CalendarIcon />
              <span>{dateLabel}</span>
            </div>
            <button type="button" className="cremation-nav-btn" onClick={() => goToDate(addDays(selectedDate, 1))} aria-label="Giorno successivo">
              ›
            </button>
            <button
              type="button"
              className={`cremation-today-btn${selectedDate === today ? " active" : ""}`}
              onClick={() => goToDate(today)}
            >
              Oggi
            </button>
          </div>
        </div>
      </div>

      <div className="cremation-summary-grid">
        <div className="dash-stat-card state-red">
          <span className="dash-stat-head">
            <span className="dash-stat-icon">
              <FlameIcon />
            </span>
            <span className="dash-stat-title">Cicli oggi</span>
          </span>
          <strong className="dash-stat-value">{totalCycles}</strong>
          <span className="dash-stat-desc">Totali</span>
        </div>
        <div className="dash-stat-card state-blue">
          <span className="dash-stat-head">
            <span className="dash-stat-icon">
              <PawIcon />
            </span>
            <span className="dash-stat-title">Animali</span>
          </span>
          <strong className="dash-stat-value">{animaliCount}</strong>
          <span className="dash-stat-desc">Totali</span>
        </div>
        <div
          className="dash-stat-card payment-due cremation-stat-clickable"
          onClick={() => setWaitingOpen((v) => !v)}
          role="button"
          tabIndex={0}
          aria-expanded={waitingOpen}
        >
          <span className="dash-stat-head">
            <span className="dash-stat-icon">
              <ClockIcon />
            </span>
            <span className="dash-stat-title">In attesa</span>
          </span>
          <strong className="dash-stat-value">{waiting?.length ?? 0}</strong>
          <span className="dash-stat-desc">Prossimi</span>
          <span className="cremation-stat-chevron" aria-hidden="true">
            <ChevronRightIcon />
          </span>
        </div>
        <div className="dash-stat-card state-blue">
          <span className="dash-stat-head">
            <span className="dash-stat-icon">
              <CheckCircleIcon />
            </span>
            <span className="dash-stat-title">Completati</span>
          </span>
          <strong className="dash-stat-value">{completedCount}</strong>
          <span className="dash-stat-desc">Oggi</span>
        </div>
        <div className="dash-stat-card state-purple">
          <span className="dash-stat-head">
            <span className="dash-stat-icon">
              <ClockIcon />
            </span>
            <span className="dash-stat-title">Fine prevista</span>
          </span>
          <strong className="dash-stat-value">{dayEnd || "-"}</strong>
          <span className="dash-stat-desc">Oggi</span>
        </div>
      </div>

      {waitingOpen && (
        <div className="cremation-waiting-panel" data-waiting-panel>
          <div className="cremation-waiting-panel-inner">
            <div className="cremation-waiting-list">
              {!waiting || waiting.length === 0 ? (
                <p className="cremation-dash">Nessun animale in attesa di pianificazione.</p>
              ) : (
                waiting.map((animal: CycleAnimal) => {
                  const [emoji, avatarCls] = speciesAvatar(animal.species);
                  return (
                    <div className={`cremation-waiting-card ${avatarCls}`} key={animal.id}>
                      <span className="cremation-animal-avatar" aria-hidden="true">
                        {emoji}
                      </span>
                      <div className="cremation-waiting-main">
                        <div className="cremation-animal-name">{animal.name || "Da inserire"}</div>
                        {animal.practice_id && (
                          <Link to={`/pratiche/${animal.practice_id}`} className="cremation-animal-open">
                            Apri pratica
                          </Link>
                        )}
                        <div className="cremation-quick-menu-wrap">
                          <button
                            type="button"
                            className="cremation-quick-insert-btn"
                            onClick={() => setOpenMenuFor(openMenuFor === animal.id ? null : animal.id)}
                          >
                            <PlusIcon width={14} height={14} />
                            <span>Inserisci</span>
                          </button>
                          {openMenuFor === animal.id && (
                            <div className="cremation-quick-menu-popover">
                              {insertableCycles.map((cycle, idx) => (
                                <button key={cycle.id} type="button" onClick={() => handleQuickInsert(animal.id, cycle.id)}>
                                  Inserisci nel Ciclo {idx + 1}
                                </button>
                              ))}
                              <button type="button" onClick={() => handleQuickCreateAndAssign(animal.id)}>
                                <PlusIcon width={14} height={14} />
                                <span>Crea nuovo ciclo</span>
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}

      {actionError && <p className="error-banner">{actionError}</p>}

      {/* V1 (progress_html, app.py:12290-12297): barra segmenti orari
          giorno-inizio -> giorno-fine con quanti cicli completati. */}
      <div className="cremation-progress">
        <span className="cremation-progress-time">{dayStart}</span>
        <div className="cremation-progress-track">
          <div className="cremation-progress-segments">
            {Array.from({ length: totalCycles }, (_, i) => (
              <span key={i} className={i < currentIndex ? "filled" : ""} />
            ))}
          </div>
          <span className="cremation-progress-label">
            {totalCycles ? `Siamo nel ciclo ${currentIndex} di ${totalCycles}` : "Nessun ciclo pianificato"}
          </span>
        </div>
        <span className="cremation-progress-time">{dayEnd}</span>
      </div>

      {/* V1 (app.py:12408-12415): <section class="cremation-column
          cremation-planning-column"> senza titolo proprio (le stat card
          sopra fanno gia' da intestazione), non una .section con <h2>
          "Cicli" - e il bottone "Aggiungi nuovo ciclo" sta qui, in fondo
          alla colonna, non come link in alto nel titlebar. V1 lo apre in
          un modal inline (cremationCreateEmptyCycle) - qui resta un link
          alla pagina di creazione gia' esistente e testata
          (/cicli-cremazione/nuovo), stessa azione reale, contenitore
          diverso (nessun modal-di-creazione-cicli ricostruito in questa
          fase). */}
      <section className="cremation-column cremation-planning-column">
        {isLoading && <p className="loading">Caricamento...</p>}
        {isError && <p className="error-banner">Errore nel caricamento dei cicli.</p>}
        {!isLoading && !isError && (
          <div className="cremation-timeline">
            {!cycles || cycles.length === 0 ? (
              <p className="cremation-dash">Nessun ciclo pianificato per questa giornata.</p>
            ) : (
              cycles.map((cycle: CremationCycle, idx: number) => {
                const dotCls =
                  cycle.status === "in_attesa" ? "cremation-dot-waiting" : cycle.status === "completato" ? "cremation-dot-done" : "cremation-dot-planned";
                const meta = CREMATION_STATUS[cycle.status];
                return (
                  <div className="cremation-timeline-item" key={cycle.id}>
                    <div className="cremation-timeline-rail">
                      <span className="cremation-timeline-time">{cycle.planned_start.slice(0, 5)}</span>
                      <span className={`cremation-timeline-dot ${dotCls}`} />
                      <span className="cremation-timeline-time">{cycle.planned_end.slice(0, 5)}</span>
                    </div>
                    <Link to={`/cicli-cremazione/${cycle.id}`} className={`cremation-cycle-card cremation-cycle-${cycle.status}`}>
                      <div className="cremation-cycle-head">
                        <span className={`cremation-cycle-number ${meta.cls}`}>CICLO {idx + 1}</span>
                        <span className="cremation-cycle-time">
                          {cycle.planned_start.slice(0, 5)} → {cycle.planned_end.slice(0, 5)}
                        </span>
                        <span className={`cremation-status-badge ${meta.cls}`}>{meta.label}</span>
                        <span className="cremation-cycle-chevron" aria-hidden="true">
                          <ChevronRightIcon />
                        </span>
                      </div>
                      <div className="cremation-week-cycle-animals">
                        {cycle.animals.length === 0 ? (
                          <span className="cremation-week-animal-line cremation-dash">Nessun animale</span>
                        ) : (
                          cycle.animals.map((animal) => (
                            <span className="cremation-week-animal-line" key={animal.id}>
                              {speciesAvatar(animal.species)[0]} {animal.name || "Da inserire"}
                            </span>
                          ))
                        )}
                      </div>
                      {cycle.status !== "completato" && (
                        <div className="cremation-cycle-action">
                          {cycle.animals.length > 0 && (
                            <button
                              type="button"
                              className="cremation-action-btn cremation-action-active"
                              onClick={(e) => {
                                e.preventDefault();
                                handleComplete(cycle.id);
                              }}
                            >
                              <CheckCircleIcon width={14} height={14} />
                              <span>Termina ciclo</span>
                            </button>
                          )}
                        </div>
                      )}
                      {cycle.status === "completato" && (
                        <div className="cremation-cycle-action">
                          <button
                            type="button"
                            className="cremation-action-btn cremation-action-planned"
                            onClick={(e) => {
                              e.preventDefault();
                              handleRevert(cycle.id);
                            }}
                          >
                            <span>Annulla completamento</span>
                          </button>
                        </div>
                      )}
                    </Link>
                  </div>
                );
              })
            )}
          </div>
        )}
        <Link className="cremation-add-cycle-btn" to="/cicli-cremazione/nuovo">
          <PlusIcon width={16} height={16} />
          <span>Aggiungi nuovo ciclo</span>
        </Link>
      </section>
    </main>
  );
}
