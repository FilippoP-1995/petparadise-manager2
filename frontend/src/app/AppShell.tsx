import { useState, type FormEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";

import {
  ArchiveIcon,
  BoxIcon,
  BuildingIcon,
  CalendarIcon,
  ChevronRightIcon,
  ClipboardIcon,
  ClockIcon,
  FlameIcon,
  HomeIcon,
  LogOutIcon,
  MoreIcon,
  PackageIcon,
  PawIcon,
  PlusIcon,
  ReceiptIcon,
  SearchIcon,
  StethoscopeIcon,
  TruckIcon,
  UsersIcon,
} from "./icons";

const TODAY_LABEL_FORMATTER = new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "2-digit", year: "numeric" });
const TODAY_WEEKDAY_FORMATTER = new Intl.DateTimeFormat("it-IT", { weekday: "long" });

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Stessa collocazione/comportamento della ricerca header di V1 (verificata
// dal vivo: placeholder "Animale o proprietario...", risultato sull'elenco
// pratiche) - qui riusa lo stesso filtro "q" gia' esistente in
// PracticeListPage/useListQueryParams invece di introdurre una seconda
// ricerca parallela: non e' una ricerca cross-dominio nuova, e' lo stesso
// filtro pratiche gia' funzionante, solo raggiungibile anche dall'header.
function HeaderSearch() {
  const navigate = useNavigate();
  const [value, setValue] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = value.trim();
    navigate(q ? `/pratiche?q=${encodeURIComponent(q)}` : "/pratiche");
  }

  return (
    <form className="app-header-search" onSubmit={handleSubmit} role="search">
      <SearchIcon width={16} height={16} />
      <input
        type="search"
        placeholder="Animale o proprietario..."
        aria-label="Ricerca pratiche per animale o proprietario"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
    </form>
  );
}

const NAV_LINKS = [
  { to: "/", label: "Dashboard", icon: HomeIcon, end: true },
  { to: "/calendario", label: "Calendario", icon: CalendarIcon },
  { to: "/pratiche", label: "Pratiche", icon: ClipboardIcon },
  { to: "/ritiri", label: "Ritiri", icon: TruckIcon },
  { to: "/riconsegne", label: "Riconsegne", icon: PackageIcon },
  { to: "/cicli-cremazione", label: "Cicli di cremazione", icon: FlameIcon },
  { to: "/fatture", label: "Fatture", icon: ReceiptIcon },
  { to: "/clienti", label: "Clienti", icon: UsersIcon },
  { to: "/veterinari", label: "Veterinari", icon: StethoscopeIcon },
  { to: "/sedi", label: "Sedi", icon: BuildingIcon },
  { to: "/catalogo-urne", label: "Catalogo Urne", icon: ArchiveIcon },
  { to: "/prodotti", label: "Prodotti", icon: BoxIcon },
];

// 5 slot reali della bottom-nav V1, letti direttamente dal markup di
// app.py (mobile_nav, non per memoria): Dashboard("/"), Calendario, il
// FAB centrale "Crea" (bottone, non un link), "Cremazioni" (icona paw-soft,
// href /programma-cremazioni), Altro. Replicato 1:1 - Pratiche torna
// nell'overflow "Altro" (in V1 non e' uno slot primario, e' raggiungibile
// da "Archivio" nel menu Altro). L'etichetta dello slot e' "Cremazioni"
// (quella breve usata da V1 nello slot stretto della bottom-nav), non
// "Cicli di cremazione" (quella usata per la voce sidebar/titolo pagina).
const BOTTOM_NAV_LEFT = [NAV_LINKS[0], NAV_LINKS[1]];
const BOTTOM_NAV_RIGHT = [{ ...NAV_LINKS[5], label: "Cremazioni", icon: PawIcon }];
const BOTTOM_NAV_PRIMARY = [NAV_LINKS[0], NAV_LINKS[1], NAV_LINKS[5]];
const BOTTOM_NAV_OVERFLOW = NAV_LINKS.filter((link) => !BOTTOM_NAV_PRIMARY.includes(link));

// V1 (MENU_CARD_META + MENU_ACCENT_COLORS, app.py:7933-7947): ogni card
// del menu "Altro" ha un sottotitolo e un colore d'accento reali, non
// piatti. Dove la voce V2 corrisponde a una voce V1 reale, stessi identici
// valori (subtitle/accent). Dove V2 ha un dominio che V1 non elenca nella
// sidebar (Ritiri/Riconsegne/Sedi sono pagine V2 dedicate - in V1 i primi
// due sono eventi calendario, non voci di menu, e Sedi vive sotto Percorso
// giornaliero), il colore resta comunque scelto dalla stessa palette reale
// (MENU_ACCENT_COLORS), mai un valore inventato fuori da quell'insieme.
const MENU_CARD_META: Record<string, { subtitle: string; accent: string }> = {
  "/pratiche": { subtitle: "Pratiche e documenti storici", accent: "#3b82f6" },
  "/ritiri": { subtitle: "Ritiri programmati", accent: "#2dd4bf" },
  "/riconsegne": { subtitle: "Riconsegne programmate", accent: "#22d3ee" },
  "/fatture": { subtitle: "Fatturazione", accent: "#3b82f6" },
  "/clienti": { subtitle: "Anagrafica clienti", accent: "#22c55e" },
  "/veterinari": { subtitle: "Anagrafica veterinari", accent: "#a855f7" },
  "/sedi": { subtitle: "Sedi aziendali", accent: "#64748b" },
  "/catalogo-urne": { subtitle: "Gestione urne e prodotti", accent: "#f59e0b" },
  "/prodotti": { subtitle: "Gestione prodotti e servizi", accent: "#3b82f6" },
};

// Azioni reali del FAB "Crea" di V1 (create-sheet, app.py:8049), lette dal
// markup - non inventate: 3 voci, "Nuova pratica" (/nuova), "Nuovo evento"
// (/calendario/nuovo - un unico form multi-tipo, non due bottoni separati
// Ritiro/Riconsegna), "Nuovo turno" (/turni/pianifica). Il dominio Turni
// non esiste in V2 (nessuna tabella, nessun modello): invece di ometterlo
// o puntare a una pagina inesistente, resta visibile ma disabilitato,
// stesso pattern gia' reale di V1 per una feature non ancora disponibile
// (vedi Cremazioni/view-tabs: <button disabled title="Non ancora disponibile">).
const CREATE_ACTIONS = [
  { to: "/pratiche/nuova", label: "Nuova pratica", icon: ClipboardIcon },
  { to: "/calendario/nuovo", label: "Nuovo evento", icon: CalendarIcon },
];

function navLinkClass({ isActive }: { isActive: boolean }) {
  return isActive ? "active" : undefined;
}

export function AppShell() {
  const { user, logout } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const today = new Date();

  return (
    <div className="app-shell">
      {/* Sidebar V1 (.top/.brand/.nav): un unico elemento che su desktop
          mostra logo+voci per esteso e su mobile collassa (solo CSS,
          nessuna variazione di markup) nel bottoncino circolare col solo
          logo in alto a sinistra - stesso comportamento reale di V1, non
          un componente separato nascosto del tutto come prima. */}
      <aside className="top">
        <a className="brand" href="/">
          <img className="brand-logo" src="/company_logo.png" alt="Pet Paradise" />
          <span className="brand-copy">
            Pet Paradise <small>MANAGER</small>
          </span>
        </a>
        <nav className="nav" aria-label="Menu principale">
          {NAV_LINKS.map(({ to, label, icon: LinkIcon, end }) => (
            <NavLink key={to} to={to} end={end} className={navLinkClass}>
              <LinkIcon />
              <span>{label}</span>
            </NavLink>
          ))}
          {user && (
            <button type="button" className="logout" onClick={() => logout()}>
              <LogOutIcon />
              <span>Esci ({user.display_name})</span>
            </button>
          )}
        </nav>
      </aside>

      {/* Header V1 (.app-header/.header-actions): un solo header, stessa
          identica struttura su desktop e mobile (ricerca + Crea + data),
          non due header diversi come prima - solo il CSS responsive
          cambia (icone-only su mobile, come in V1). */}
      <header className="app-header">
        <div className="header-actions">
          <HeaderSearch />
          <button type="button" className="btn header-new" onClick={() => setCreateOpen(true)} aria-label="Crea pratica o evento">
            <PlusIcon width={18} height={18} />
            <span>Crea</span>
          </button>
          <time dateTime={today.toISOString().slice(0, 10)} className="app-header-date">
            {TODAY_LABEL_FORMATTER.format(today)}
            <small>{capitalize(TODAY_WEEKDAY_FORMATTER.format(today))}</small>
          </time>
        </div>
      </header>

      <div className="app-main">
        <Outlet />
      </div>

      <nav className="bottom-nav" aria-label="Navigazione mobile">
        {BOTTOM_NAV_LEFT.map(({ to, label, icon: LinkIcon, end }) => (
          <NavLink key={to} to={to} end={end} className={navLinkClass}>
            <LinkIcon />
            <span>{label}</span>
          </NavLink>
        ))}
        <button type="button" className="bottom-nav-fab" onClick={() => setCreateOpen(true)} aria-label="Crea">
          <PlusIcon />
        </button>
        {BOTTOM_NAV_RIGHT.map(({ to, label, icon: LinkIcon, end }) => (
          <NavLink key={to} to={to} end={end} className={navLinkClass}>
            <LinkIcon />
            <span>{label}</span>
          </NavLink>
        ))}
        <button type="button" onClick={() => setMoreOpen(true)} aria-label="Altre pagine">
          <MoreIcon />
          <span>Altro</span>
        </button>
      </nav>

      {createOpen && (
        <>
          <div className="create-sheet-backdrop" onClick={() => setCreateOpen(false)} />
          <aside className="create-sheet" aria-label="Crea">
            {CREATE_ACTIONS.map(({ to, label, icon: ActionIcon }) => (
              <NavLink key={to} to={to} onClick={() => setCreateOpen(false)}>
                <ActionIcon width={18} height={18} />
                <span>{label}</span>
              </NavLink>
            ))}
            <button type="button" disabled title="Non ancora disponibile">
              <ClockIcon width={18} height={18} />
              <span>Nuovo turno</span>
            </button>
          </aside>
        </>
      )}

      {moreOpen && (
        <>
          <div className="more-backdrop" onClick={() => setMoreOpen(false)} />
          {/* V1 (.more-title/.more-card-list, app.py:8046-8047): titolo
              "Menu"/"Tutto a portata di mano" + card con icona colorata,
              titolo, sottotitolo e chevron - non un elenco di link piatto.
              Il drag-to-reorder di V1 (toggleMoreMenuEdit, salvataggio
              ordine su /il-mio-profilo/salva) resta fuori: richiede un
              dominio "preferenze utente" che V2 non ha ancora - qui
              l'ordine e' sempre quello fisso di NAV_LINKS. */}
          <aside className="more-menu" aria-label="Altre funzioni">
            <div className="more-title">
              <div>
                <b>Menu</b>
                <small>Tutto a portata di mano</small>
              </div>
              {/* V1 (app.py:8049): il bottone e' letteralmente il carattere
                  "×", non un'icona lucide - .more-close-btn e' pensato per
                  quel glifo (font-size:18px, line-height:1), non per un'SVG. */}
              <button type="button" className="more-close-btn" onClick={() => setMoreOpen(false)} aria-label="Chiudi">
                ×
              </button>
            </div>
            <div className="more-card-list">
              {BOTTOM_NAV_OVERFLOW.map(({ to, label, icon: LinkIcon }) => {
                const meta = MENU_CARD_META[to];
                return (
                  <NavLink
                    key={to}
                    to={to}
                    className="more-card"
                    style={meta ? ({ "--accent": meta.accent } as React.CSSProperties) : undefined}
                    onClick={() => setMoreOpen(false)}
                  >
                    <span className="more-card-icon">
                      <LinkIcon />
                    </span>
                    <span className="more-card-copy">
                      <b>{label}</b>
                      {meta && <small>{meta.subtitle}</small>}
                    </span>
                    <span className="more-card-chevron">
                      <ChevronRightIcon />
                    </span>
                  </NavLink>
                );
              })}
            </div>
            {user && (
              <div className="logout-row">
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false);
                    logout();
                  }}
                >
                  <LogOutIcon /> Esci ({user.display_name})
                </button>
              </div>
            )}
          </aside>
        </>
      )}
    </div>
  );
}
