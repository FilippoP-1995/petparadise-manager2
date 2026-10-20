import type { SVGProps } from "react";

/**
 * Icone = i path SVG reali usati da V1 (LUCIDE_PATHS/lucide(), app.py
 * ~6996-7049), copiati alla lettera - non ricostruiti a memoria. Stesso
 * viewBox 24x24, stesso stroke-width 1.8, stesso fill/linecap/linejoin.
 * Dove V2 ha un dominio che V1 non ha come voce di navigazione distinta
 * (Riconsegne/Sedi/Prodotti), l'icona e' comunque presa dal set reale di
 * LUCIDE_PATHS (mai disegnata da zero) scegliendo la voce concettualmente
 * piu' vicina gia' usata da V1 altrove.
 */
function Icon({ children, ...props }: SVGProps<SVGSVGElement> & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      width={20}
      height={20}
      {...props}
    >
      {children}
    </svg>
  );
}

// V1 "home"
export const HomeIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="m3 11 9-8 9 8" />
    <path d="M5 10v10h14V10" />
    <path d="M9 20v-6h6v6" />
  </Icon>
);

// V1 "search"
export const SearchIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx="11" cy="11" r="8" />
    <path d="m21 21-4.3-4.3" />
  </Icon>
);

// V1 "plus"
export const PlusIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);

// V1 "calendar"
export const CalendarIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect width="18" height="18" x="3" y="4" rx="2" />
    <path d="M16 2v4M8 2v4M3 10h18" />
  </Icon>
);

// V1 "clipboard"
export const ClipboardIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect width="16" height="18" x="4" y="4" rx="2" />
    <path d="M9 4V2h6v2M8 9h8M8 13h6" />
  </Icon>
);

// V1 "truck"
export const TruckIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M10 17h4V5H2v12h3" />
    <path d="M20 17h2v-3.34a4 4 0 0 0-1.17-2.83L19 9h-5v8h1" />
    <circle cx="7.5" cy="17.5" r="2.5" />
    <circle cx="17.5" cy="17.5" r="2.5" />
  </Icon>
);

// V2 Riconsegne non e' una voce di navigazione distinta in V1 (li' e' un
// evento calendario di tipo "Riconsegna"): icona presa da V1 "gift"
// (unico path reale del set con connotazione "consegna/pacco"), non
// disegnata ex novo.
export const PackageIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="3" y="8" width="18" height="4" rx="1" />
    <path d="M12 8v13" />
    <path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7" />
    <path d="M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5" />
  </Icon>
);

// V1 "flame"
export const FlameIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z" />
  </Icon>
);

// V1 "receipt"
export const ReceiptIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M4 2v20l3-2 3 2 2-2 3 2 2-2 3 2V2l-3 2-3-2-2 2-3-2-2 2Z" />
    <path d="M8 9h8M8 13h6" />
  </Icon>
);

// V1 "users"
export const UsersIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
  </Icon>
);

// V1 "stethoscope"
export const StethoscopeIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M11 2v2M5 2v2M5 3H3v4a6 6 0 0 0 12 0V3h-2" />
    <circle cx="18" cy="16" r="3" />
    <path d="M15 16H9a4 4 0 0 1-4-4v-1" />
  </Icon>
);

// V2 Sedi non e' una voce di navigazione distinta in V1: icona presa da
// V1 "map-pin" (path reale, connotazione "luogo/sede"), non disegnata
// ex novo.
export const BuildingIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
    <circle cx="12" cy="10" r="3" />
  </Icon>
);

// V1 "archive"
export const ArchiveIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect width="18" height="4" x="3" y="3" rx="1" />
    <path d="M5 7v13h14V7M9 11h6" />
  </Icon>
);

// V2 Prodotti: V1 usa realmente "clipboard" per la sua voce "Prodotti"
// (SIDEBAR_LINKS), ma quel path e' gia' assegnato a Pratiche nel sidebar
// V2 (avrebbero due icone identiche nella stessa lista) - presa quindi
// "briefcase" (path reale V1, usato per "Collaboratori", connotazione
// "materiali/forniture"), non disegnata ex novo.
export const BoxIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect width="20" height="14" x="2" y="7" rx="2" />
    <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
  </Icon>
);

// V1 "more-horizontal"
export const MoreIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
    <circle cx="5" cy="12" r="1" />
  </Icon>
);

// V1 "more-vertical" (LUCIDE_PATHS, app.py:7025) - usata dal menu "Altro"
// dei riepiloghi evento/ritiro/riconsegna (calendar-detail-topbar-menu-btn),
// diversa da MoreIcon (quella e' "more-horizontal", usata altrove).
export const MoreVerticalIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx="12" cy="12" r="1" />
    <circle cx="12" cy="5" r="1" />
    <circle cx="12" cy="19" r="1" />
  </Icon>
);

// V1 usa realmente "menu" (le 3 barre orizzontali) per il link "Esci",
// non un'icona di logout dedicata (vedi nav in layout(), app.py:8039:
// <a class="logout" href="/logout">{lucide("menu")}<span>Esci</span></a>).
export const LogOutIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Icon>
);

// V1 "x"
export const XIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </Icon>
);

// V1 "wallet"
export const WalletIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M20 7V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h15v12H5a3 3 0 0 1-3-3V6" />
    <path d="M16 13h2" />
  </Icon>
);

// V1 "clock" (app.py:7028)
export const ClockIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx={12} cy={12} r={10} />
    <path d="M12 6v6l4 2" />
  </Icon>
);

// V1 "phone" (LUCIDE_PATHS, app.py:7021) - usata da phone_action_buttons per il call-btn.
export const PhoneIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" />
  </Icon>
);

// V1 "message" (LUCIDE_PATHS, app.py:6998) - usata da phone_action_buttons per il whatsapp-btn.
export const MessageIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4Z" />
    <path d="M8 9h8M8 13h5" />
  </Icon>
);

// V1 "check-circle" (app.py:7030)
export const CheckCircleIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
    <path d="m9 11 3 3L22 4" />
  </Icon>
);

// V1 "navigation" (app.py:7035)
export const NavigationIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <polygon points="3 11 22 2 13 21 11 13 3 11" />
  </Icon>
);

// V1 "sun"
export const SunIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx={12} cy={12} r={4} />
    <path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" />
  </Icon>
);

// V1 "chart"
export const ChartIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M3 3v18h18" />
    <path d="m7 16 4-5 4 3 5-7" />
  </Icon>
);

// V1 "chevron-right"
export const ChevronRightIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="m9 18 6-6-6-6" />
  </Icon>
);

// V1 "paw-soft" (bottom-nav, voce Cremazioni): stessi 5 cerchi, stessi centri/raggi.
export const PawIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx="11" cy="16.6" r="4.6" />
    <circle cx="5" cy="10" r="2.3" />
    <circle cx="10" cy="5.8" r="2.3" />
    <circle cx="16" cy="6.8" r="2.3" />
    <circle cx="19" cy="12" r="2.3" />
  </Icon>
);

// V1 "bell" (LUCIDE_PATHS) - usata da mobile_nav (notifiche) e dal
// type-picker "Nuovo evento" (Promemoria).
export const BellIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
  </Icon>
);

// V1 "user" (app.py:7022) - persona singola, diversa da UsersIcon (icona
// "users" multi-persona, gia' usata per la voce sidebar Clienti). Usata dai
// calendar-tap-card di "Nuovo evento" (Operatore, Cliente/Proprietario).
export const UserIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </Icon>
);

// V1 "save" (app.py:7016) - usata dal bottone "Salva evento" del wizard
// calendario.
export const SaveIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
    <path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7" />
    <path d="M7 3v4a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V3.4" />
  </Icon>
);

// V1 "arrow-up" (app.py:7018) - usata dalla freccia della quick-stat-delta
// quando dir="up" (quick_stat_card, app.py:8821): un'icona reale, non il
// carattere "↑" (quello resta solo per il badge Analytics, che in V1 usa
// davvero i caratteri unicode "↑"/"↓").
export const ArrowUpIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M12 19V5" />
    <path d="m5 12 7-7 7 7" />
  </Icon>
);

// V1 "arrow-down" (app.py:7017) - vedi ArrowUpIcon.
export const ArrowDownIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M12 5v14" />
    <path d="m19 12-7 7-7-7" />
  </Icon>
);
