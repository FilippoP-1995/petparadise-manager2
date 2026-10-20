import { useState, type ComponentType, type ReactNode } from "react";
import { Link } from "react-router-dom";

import {
  ArchiveIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  CalendarIcon,
  ChartIcon,
  CheckCircleIcon,
  ChevronRightIcon,
  HomeIcon,
  ReceiptIcon,
  SearchIcon,
  SunIcon,
  TruckIcon,
  WalletIcon,
} from "@/app/icons";
import { useAuth } from "@/features/auth/useAuth";
import { formatMoney } from "@/shared/money";
import { speciesAvatar } from "@/shared/speciesAvatar";

import { useDashboardSummary, type DashboardPeriod } from "./api";
import "./dashboard.css";

type DailyIncasso = { date: string; amount_cents: number };

// V1 (dashboard_delta_pct): stessa formula, stessa soglia "flat" quando il
// valore precedente e' 0 - non un calcolo percentuale reinventato.
function deltaPct(current: number, previous: number): { pct: number; dir: "up" | "down" | "flat" } {
  if (previous === 0) {
    if (current === 0) return { pct: 0, dir: "flat" };
    return { pct: 100, dir: "up" };
  }
  const pct = Math.round(((current - previous) / previous) * 100);
  if (pct > 0) return { pct, dir: "up" };
  if (pct < 0) return { pct: Math.abs(pct), dir: "down" };
  return { pct: 0, dir: "flat" };
}

// V1 (incassi_chart_html, app.py:8762): il badge Analytics usa davvero i
// caratteri unicode "↑"/"↓"/"–" - riservati a quel solo badge.
const DELTA_ARROW: Record<"up" | "down" | "flat", string> = { up: "↑", down: "↓", flat: "–" };

// V1 (dashboard_sparkline_points): normalizzazione min/max locale su un
// viewBox 0 0 100 32, stessa funzione riusata da ogni sparkline V1.
function sparklinePoints(values: number[]): string {
  if (values.length === 0) return "";
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const step = 100 / Math.max(1, values.length - 1);
  return values.map((v, i) => `${(i * step).toFixed(1)},${(32 - ((v - lo) / span) * 28 - 2).toFixed(1)}`).join(" ");
}

// V1 (line_chart_svg, app.py:8737-8755): polyline + area riempita + punti,
// stesso viewBox 560x140, stesso algoritmo di normalizzazione min/max.
function LineChart({ values }: { values: number[] }) {
  const width = 560;
  const height = 140;
  const padX = 10;
  const padY = 16;
  if (values.length === 0) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const step = (width - 2 * padX) / Math.max(1, values.length - 1);
  const points = values.map((v, i) => {
    const x = padX + i * step;
    const y = hi === lo ? height / 2 : padY + (height - 2 * padY) * (1 - (v - lo) / span);
    return [x, y] as const;
  });
  const poly = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area =
    `M${points[0][0].toFixed(1)},${(height - padY).toFixed(1)} ` +
    points.map(([x, y]) => `L${x.toFixed(1)},${y.toFixed(1)}`).join(" ") +
    ` L${points[points.length - 1][0].toFixed(1)},${(height - padY).toFixed(1)} Z`;
  return (
    <svg className="dashboard-linechart-svg" viewBox={`0 0 ${width} ${height}`}>
      <path className="dashboard-linechart-area" d={area} />
      <polyline className="dashboard-linechart-line" points={poly} />
      {points.map(([x, y], i) => (
        <circle key={i} className="dashboard-linechart-dot" cx={x} cy={y} r={4} />
      ))}
    </svg>
  );
}

// V1 (donut_svg, app.py:8766-8783): stessi 4 segmenti/colori del donut
// "Pratiche per stato", dati riusati 1:1 da practice_status_counts.
const DONUT_SEGMENTS: { key: "ritirato" | "in_programma" | "da_consegnare" | "consegnato"; label: string; color: string }[] = [
  { key: "ritirato", label: "Ritirati", color: "#fde047" },
  { key: "in_programma", label: "In programma", color: "#fb7185" },
  { key: "da_consegnare", label: "Da consegnare", color: "#c084fc" },
  { key: "consegnato", label: "Consegnati", color: "#4ade80" },
];

function Donut({ counts }: { counts: Record<string, number> }) {
  const size = 176;
  const stroke = 22;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const cx = size / 2;
  const cy = size / 2;
  const total = DONUT_SEGMENTS.reduce((sum, seg) => sum + (counts[seg.key] ?? 0), 0);
  let offset = 0;
  return (
    <div className="analytics-donut-row">
      <svg className="dashboard-donut-svg" viewBox={`0 0 ${size} ${size}`}>
        <circle cx={cx} cy={cy} r={radius} stroke="#334155" strokeWidth={stroke} fill="none" />
        {DONUT_SEGMENTS.map((seg) => {
          const value = counts[seg.key] ?? 0;
          const frac = total ? value / total : 0;
          const length = frac * circumference;
          const dash = `${length.toFixed(2)} ${(circumference - length).toFixed(2)}`;
          const el = (
            <circle
              key={seg.key}
              className="dashboard-donut-arc"
              cx={cx}
              cy={cy}
              r={radius}
              stroke={seg.color}
              strokeWidth={stroke}
              fill="none"
              strokeDasharray={dash}
              strokeDashoffset={-offset}
              transform={`rotate(-90 ${cx} ${cy})`}
            />
          );
          offset += length;
          return el;
        })}
        <text className="dashboard-donut-total" x={cx} y={cy - 6} textAnchor="middle">
          {total}
        </text>
        <text className="dashboard-donut-total-label" x={cx} y={cy + 14} textAnchor="middle">
          Totale
        </text>
      </svg>
      <ul className="dashboard-donut-legend">
        {DONUT_SEGMENTS.map((seg) => {
          const value = counts[seg.key] ?? 0;
          const pct = total ? Math.round((100 * value) / total) : 0;
          return (
            <li key={seg.key}>
              <span className="dashboard-donut-dot" style={{ background: seg.color }} />
              {seg.label}
              <b>
                {value} ({pct}%)
              </b>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// V1 (quick_stat_card, app.py:8818-8829): icona, valore, sparkline,
// variazione % vs ieri con freccia colorata verde/rosso/grigia.
function QuickStatCard({
  icon: IconComponent,
  cls,
  label,
  value,
  trend,
  delta,
}: {
  icon: ComponentType<{ width?: number; height?: number }>;
  cls: string;
  label: string;
  value: string;
  trend: number[];
  delta: { pct: number; dir: "up" | "down" | "flat" };
}) {
  const points = sparklinePoints(trend);
  return (
    <div className={`quick-stat-card ${cls}`}>
      <span className="quick-stat-icon">
        <IconComponent width={17} height={17} />
      </span>
      <div className="quick-stat-right">
        <div className="quick-stat-body">
          <b>{value}</b>
          <small>{label}</small>
        </div>
        {points && (
          <svg className="quick-stat-spark" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true">
            <polyline points={points} />
          </svg>
        )}
        <span className={`quick-stat-delta quick-stat-delta-${delta.dir}`}>
          {/* V1 (quick_stat_card, app.py:8821): freccia icona lucide reale
              per up/down, non il carattere unicode (quello resta solo per
              il badge Analytics sopra). */}
          {delta.dir === "up" && <ArrowUpIcon width={12} height={12} />}
          {delta.dir === "down" && <ArrowDownIcon width={12} height={12} />}
          {delta.dir === "flat" && DELTA_ARROW.flat}
          {" "}
          {delta.pct}%<small>vs ieri</small>
        </span>
      </div>
    </div>
  );
}

// Stesso ordine/sottoinsieme delle 4 card di stato piu' rilevanti mostrate
// in evidenza in V1 - "Cremati" e "Smaltiti" restano comunque visibili
// nell'elenco Pratiche filtrabile, qui la Dashboard privilegia densita' e
// leggibilita' sulle 4 fasi operative piu' frequenti.
// Icona/classe colore = V1 state_specs (app.py:8666-8670): stesse 4 righe,
// stessa icona, stesso stato-colore.
const PRIMARY_PRACTICE_STATUSES = [
  { key: "ritirato", label: "Ritirati", icon: ArchiveIcon, cls: "state-yellow", desc: "Apri elenco" },
  { key: "in_programma", label: "In programma", icon: CalendarIcon, cls: "state-red", desc: "Apri elenco" },
  { key: "da_consegnare", label: "Da consegnare", icon: TruckIcon, cls: "state-purple", desc: "Aperti, senza scadenza" },
  { key: "consegnato", label: "Consegnati", icon: HomeIcon, cls: "state-green", desc: "Apri elenco" },
] as const;


const PERIOD_LABELS: Record<DashboardPeriod, string> = { oggi: "Oggi", settimana: "Settimana", mese: "Mese" };

const WEEKDAY_LABELS = ["Dom", "Lun", "Mar", "Mer", "Gio", "Ven", "Sab"];

function PeriodSelector({ value, onChange }: { value: DashboardPeriod; onChange: (period: DashboardPeriod) => void }) {
  return (
    <div className="period-selector" role="tablist" aria-label="Periodo">
      {(Object.keys(PERIOD_LABELS) as DashboardPeriod[]).map((period) => (
        <button
          key={period}
          type="button"
          role="tab"
          aria-selected={value === period}
          className={value === period ? "active" : undefined}
          onClick={() => onChange(period)}
        >
          {PERIOD_LABELS[period]}
        </button>
      ))}
    </div>
  );
}

// V1 (dash_stat_card, app.py:8671-8683): badge icona colorata, icona di
// sfondo semi-trasparente (o carattere "€" per il totale incassato),
// chevron, valore grande - stessa struttura reale, non la card generica
// di prima. La sparkline per-card di V1 non e' replicata: richiederebbe
// un trend giornaliero per singolo stato/canale che l'API dashboard V2
// non restituisce oggi (solo il totale incassi/giorno, gia' usato
// nell'Analytics sotto) - vedi report.
function DashStatCard({
  cls,
  icon: IconComponent,
  bgChar,
  label,
  value,
  desc,
  href,
}: {
  cls: string;
  icon?: ComponentType<{ width?: number; height?: number }>;
  bgChar?: string;
  label: string;
  value: string;
  desc: ReactNode;
  href?: string;
}) {
  const content = (
    <>
      <span className={bgChar ? "dash-stat-bgicon dash-stat-bgicon-char" : "dash-stat-bgicon"} aria-hidden="true">
        {bgChar ?? (IconComponent && <IconComponent width={24} height={24} />)}
      </span>
      <span className="dash-stat-head">
        <span className="dash-stat-icon">{IconComponent && <IconComponent width={17} height={17} />}</span>
        <span className="dash-stat-title">{label}</span>
      </span>
      <strong className="dash-stat-value">{value}</strong>
      <span className="dash-stat-desc">{desc}</span>
      <span className="dash-stat-chevron" aria-hidden="true">
        <ChevronRightIcon width={15} height={15} />
      </span>
    </>
  );
  return href ? (
    <Link className={`dash-stat-card ${cls}`} to={href}>
      {content}
    </Link>
  ) : (
    <div className={`dash-stat-card ${cls}`}>{content}</div>
  );
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 13) return "Buongiorno";
  if (hour < 18) return "Buon pomeriggio";
  return "Buonasera";
}

export function DashboardPage() {
  const [period, setPeriod] = useState<DashboardPeriod>("oggi");
  const { data, isLoading, isError } = useDashboardSummary(period);
  const { user } = useAuth();

  return (
    <main className="wrap dashboard-wrap">
      {/* V1 (dashboard()): niente titlebar generico con bottone "+ Nuova
          pratica" (quello e' il pattern delle pagine elenco) - la
          Dashboard ha una sezione di benvenuto propria. */}
      <section className="welcome">
        <div>
          <h1>
            {greeting()}, {user?.display_name} <span aria-hidden="true">👋</span>
          </h1>
          <p>Panoramica operativa del periodo selezionato</p>
        </div>
      </section>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Impossibile caricare la dashboard.</p>}

      {data && (
        <>
          {/* V1 (reminders_html, app.py:8972-8980): .reminders-card >
              .reminders-carousel-row > .reminders-carousel (una .reminders-
              slide per TIPO di promemoria attivo, scroll-snap verticale,
              colore/icona per categoria da REMINDER_GROUP_LABELS) +
              .reminders-side-col con i dots. V2 ha un solo tipo di dato
              reale (payment_reminders, il sottoinsieme piu' vicino a
              "delivered_unpaid" - icona/colore ripresi da li'), quindi una
              sola slide aggregata invece del carosello multi-tipo di V1 -
              non un carosello a piu' pagine se c'e' una sola voce, nessuno
              swipe da inventare per una sola slide. Il bottone "+"
              (promemoria manuali custom) resta fuori: nessuna tabella
              promemoria-manuali in V2 (gap di dominio, disclosed). */}
          <section className="reminders-card">
            <div className="reminders-carousel-row">
              <div className="reminders-carousel">
                {data.payment_reminders.length > 0 ? (
                  <div className="reminders-slide">
                    <Link className="reminders-slide-front" style={{ borderLeft: "3px solid #facc15" }} to="/pratiche">
                      <span className="metric-icon reminders-slide-icon state-red">
                        <CalendarIcon />
                      </span>
                      <span className="reminders-slide-copy">
                        <b>
                          {data.payment_reminders.length}{" "}
                          {data.payment_reminders.length === 1
                            ? "pratica con un residuo da incassare"
                            : "pratiche con un residuo da incassare"}
                        </b>
                        <small>
                          Dalla più vecchia: {data.payment_reminders[0].practice_number} - residuo{" "}
                          {formatMoney(data.payment_reminders[0].residual_cents)}
                        </small>
                      </span>
                    </Link>
                  </div>
                ) : (
                  <div className="reminders-slide reminders-slide-empty">
                    <div className="reminders-slide-front" style={{ borderLeft: "3px solid #64748b" }}>
                      <span className="metric-icon reminders-slide-icon state-blue">
                        <CheckCircleIcon />
                      </span>
                      <span className="reminders-slide-copy">
                        <b>Nessun promemoria attivo</b>
                        <small>Sei in pari con tutto</small>
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* V1 (reminders_todo_list, sottoinsieme espanso del promemoria
              "delivered_unpaid"): elenco puntuale delle singole pratiche -
              qui mantenuto SOTTO la slide aggregata cosi' il dettaglio per
              pratica (gia' utile e reale) non va perso, invece di sceglierne
              uno a scapito dell'altro. */}
          {data.payment_reminders.length > 0 && (
            <div className="card">
              <h2>Da seguire</h2>
              <p className="sub">Pratiche con un residuo da incassare, dalla più vecchia.</p>
              <ul className="reminders-todo-list">
                {data.payment_reminders.map((reminder) => (
                  <li key={reminder.practice_id}>
                    <Link className="reminders-todo-row" to={`/pratiche/${reminder.practice_id}`}>
                      <b>{reminder.practice_number}</b> - residuo {formatMoney(reminder.residual_cents)}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="dashboard-section-head">
            <h2>Pratiche</h2>
            <PeriodSelector value={period} onChange={setPeriod} />
          </div>
          <div className="dashboard-states">
            {PRIMARY_PRACTICE_STATUSES.map(({ key, label, icon, cls, desc }) => (
              <DashStatCard
                key={key}
                cls={cls}
                icon={icon}
                label={label}
                value={String(data.practice_status_counts[key as keyof typeof data.practice_status_counts])}
                desc={desc}
                href={`/pratiche?status=${key}`}
              />
            ))}
          </div>

          <div className="dashboard-section-head">
            <h2>Pagamenti</h2>
          </div>
          {/* V1 (payment_specs, app.py:8697-8715): "Da saldare" e' sempre
              la fotografia corrente (mai filtrata per periodo), "Acconti"/
              "Pagati" contano i MOVIMENTI avvenuti nel periodo selezionato
              (non le pratiche) - stessa distinzione reale di V1, non piu'
              una classificazione per-pratica generica a 4 stati. */}
          <div className="dashboard-payments">
            {/* V1 (dash_stat_card desc, app.py:8709): l'importo e la
                didascalia sono due righe separate (<small> a blocco sotto
                l'importo), non una singola riga con un separatore " · "
                inventato. */}
            <DashStatCard
              cls="payment-due"
              icon={WalletIcon}
              label="Da saldare"
              value={String(data.pagamenti_da_saldare_count)}
              desc={
                <>
                  {formatMoney(data.pagamenti_da_saldare_totale_cents)}
                  <small>Tutte le rimanenze aperte</small>
                </>
              }
            />
            <DashStatCard
              cls="payment-deposit"
              icon={ReceiptIcon}
              label="Acconti"
              value={String(data.pagamenti_acconto_count_periodo)}
              desc={
                <>
                  {formatMoney(data.pagamenti_acconto_totale_cents_periodo)}
                  <small>movimenti nel periodo</small>
                </>
              }
            />
            <DashStatCard
              cls="payment-paid"
              icon={ChartIcon}
              label="Pagati"
              value={String(data.pagamenti_pagato_count_periodo)}
              desc={
                <>
                  {formatMoney(data.pagamenti_pagato_totale_cents_periodo)}
                  <small>movimenti nel periodo</small>
                </>
              }
            />
            <DashStatCard
              cls="payment-total"
              bgChar="€"
              label="Totale incassato"
              value={formatMoney(data.incasso_periodo_cents)}
              desc={PERIOD_LABELS[period]}
            />
          </div>

          {/* V1 (dashboard(): sezione ANALYTICS, app.py:8759-8830) - grafico
              incassi ultimi 7 giorni con delta badge "vs 7 giorni
              precedenti" + donut "Pratiche per stato", stessi dati gia'
              mostrati dalle card sopra (mai una seconda fonte). Il
              carosello Oggi/Settimana/Mese swipeabile di V1 non e'
              replicato qui: l'Analytics segue semplicemente la stessa
              pillola "Pratiche" gia' in cima alla pagina - vedi report. */}
          <section className="dashboard-analytics">
            <div className="dashboard-section-head">
              <h2 className="dashboard-heading">Analytics</h2>
            </div>
            <div className="dashboard-analytics-grid">
              <AnalyticsIncassiPanel dailyIncassi={data.daily_incassi} previous7dCents={data.incasso_precedenti_7_giorni_cents} />
              <div className="analytics-panel analytics-donut-panel">
                <h3 className="analytics-panel-title">Pratiche per stato</h3>
                <Donut counts={data.practice_status_counts} />
              </div>
            </div>
          </section>

          {/* V1 (quick_specs, app.py:8805-8830) - stesse 4 metriche/stesso
              proxy "Da saldare" gia' documentato da V1 stesso come tale. */}
          <section className="dashboard-quickstats">
            <QuickStatCard
              icon={SearchIcon}
              cls="state-blue"
              label="Pratiche oggi"
              value={String(data.pratiche_ritirato_oggi)}
              trend={data.daily_ritirato.map((d) => d.count)}
              delta={deltaPct(data.pratiche_ritirato_oggi, data.pratiche_ritirato_ieri)}
            />
            <QuickStatCard
              icon={SunIcon}
              cls="payment-paid"
              label="Incassi oggi"
              value={formatMoney(data.incasso_oggi_cents)}
              trend={data.daily_incassi.map((d) => d.amount_cents)}
              delta={deltaPct(data.incasso_oggi_cents, data.incasso_ieri_cents)}
            />
            <QuickStatCard
              icon={WalletIcon}
              cls="payment-due"
              label="Da saldare"
              value={String(data.pagamenti_da_saldare_count)}
              trend={data.daily_da_saldare_proxy.map((d) => d.count)}
              delta={deltaPct(data.pratiche_da_saldare_oggi, data.pratiche_da_saldare_ieri)}
            />
            <QuickStatCard
              icon={CalendarIcon}
              cls="state-blue"
              label="Nuove pratiche"
              value={String(data.nuove_pratiche_oggi)}
              trend={data.daily_nuove.map((d) => d.count)}
              delta={deltaPct(data.nuove_pratiche_oggi, data.nuove_pratiche_ieri)}
            />
          </section>

          {/* V1 (recent_practice_cards_html, app.py:14276-14324): card con
              avatar specie/bordo colorato, non un elenco testuale. */}
          <section className="dashboard-recent">
            {/* V1 (app.py:8834): titolo reale "...per data recupero" - la
                pagina ordina per data di recupero (pickup_date, fallback
                data creazione), non solo per data creazione: vedi gap
                disclosed in backend/services/dashboard_service.py (ordina
                solo per created_at) nel report. */}
            <div className="titlebar">
              <h2>Ultime 10 pratiche per data recupero</h2>
              <Link to="/pratiche">
                Apri archivio<span className="recent-practice-archive-arrow" aria-hidden="true">›</span>
              </Link>
            </div>
            {data.recent_practices.length === 0 && <p className="empty-state">Nessuna pratica presente.</p>}
            {data.recent_practices.length > 0 && (
              <div className="recent-practice-list">
                {data.recent_practices.map((practice) => {
                  const [avatarEmoji, avatarCls] = speciesAvatar(practice.primary_species);
                  return (
                    <Link key={practice.id} className={`recent-practice-card ${avatarCls}`} to={`/pratiche/${practice.id}`}>
                      <div className="recent-practice-animal">
                        <span className="recent-practice-avatar">{avatarEmoji}</span>
                        <div className="recent-practice-animal-copy">
                          <span className="recent-practice-name">{practice.animal_names || practice.practice_number}</span>
                          <span className="recent-practice-meta">{practice.practice_number}</span>
                        </div>
                      </div>
                      {/* V1 (recent_practice_cards_html, app.py:14793-14837): questa
                          card NON mostra mai un badge di stato - solo data
                          (+ orario ritiro se presente, non disponibile qui) -
                          un badge qui sarebbe un elemento inventato, non
                          presente in V1 in questo punto preciso. */}
                      <div className="recent-practice-date">
                        <span className="recent-practice-date-value">
                          <CalendarIcon width={14} height={14} />
                          {new Date(practice.created_at).toLocaleDateString("it-IT")}
                        </span>
                      </div>
                      <div className="recent-practice-owner">
                        <span className="recent-practice-owner-name">{practice.owner_display_name || "Senza proprietario"}</span>
                      </div>
                      <span className="recent-practice-chevron" aria-hidden="true">
                        <ChevronRightIcon width={20} height={20} />
                      </span>
                    </Link>
                  );
                })}
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}

// V1 (incassi_chart_html, app.py:8759-8765): valore totale, delta badge
// "vs 7 giorni precedenti", grafico a linea, etichette giorno sotto.
function AnalyticsIncassiPanel({ dailyIncassi, previous7dCents }: { dailyIncassi: DailyIncasso[]; previous7dCents: number }) {
  const total7d = dailyIncassi.reduce((sum, d) => sum + d.amount_cents, 0);
  const delta = deltaPct(total7d, previous7dCents);
  return (
    <div className="analytics-panel">
      <h3 className="analytics-panel-title">Incassi ultimi 7 giorni</h3>
      <div className="analytics-linechart-value">
        {formatMoney(total7d)}
        <small>Totale</small>
      </div>
      <span className={`analytics-delta-badge analytics-delta-${delta.dir}`}>
        {DELTA_ARROW[delta.dir]} {delta.pct}%<small>vs 7 giorni precedenti</small>
      </span>
      <LineChart values={dailyIncassi.map((d) => d.amount_cents)} />
      <div className="analytics-linechart-labels">
        {dailyIncassi.map((d) => (
          <span key={d.date}>{WEEKDAY_LABELS[new Date(`${d.date}T00:00:00Z`).getUTCDay()]}</span>
        ))}
      </div>
    </div>
  );
}
