import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "@/features/auth/useAuth";

import {
  ArchiveIcon,
  BoxIcon,
  BuildingIcon,
  CalendarIcon,
  ClipboardIcon,
  FlameIcon,
  LogOutIcon,
  MoreIcon,
  PackageIcon,
  ReceiptIcon,
  StethoscopeIcon,
  TruckIcon,
  UsersIcon,
  XIcon,
} from "./icons";

const NAV_LINKS = [
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

// Stesso principio dei 5 slot della bottom-nav V1 (4 fissi + un
// overflow), ma senza il FAB di creazione rapida ne' una voce
// "Dashboard" che in V2 non esiste - nessuna nuova destinazione o
// azione, solo la navigazione gia' presente riorganizzata per stare in
// 5 slot su schermi stretti.
const BOTTOM_NAV_PRIMARY = NAV_LINKS.slice(0, 4);
const BOTTOM_NAV_OVERFLOW = NAV_LINKS.slice(4);

function navLinkClass({ isActive }: { isActive: boolean }) {
  return isActive ? "active" : undefined;
}

export function AppShell() {
  const { user, logout } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand">Pet Paradise Manager V2</div>
        <nav className="sidebar-nav">
          {NAV_LINKS.map(({ to, label, icon: LinkIcon }) => (
            <NavLink key={to} to={to} className={navLinkClass}>
              <LinkIcon />
              {label}
            </NavLink>
          ))}
        </nav>
        {user && (
          <div className="sidebar-user">
            <div className="who">
              <b>{user.display_name}</b>
              {user.role}
            </div>
            <button className="btn-ghost" onClick={() => logout()}>
              <LogOutIcon /> Esci
            </button>
          </div>
        )}
      </aside>

      <div className="app-main">
        <header className="mobile-topbar">
          <span className="brand">Pet Paradise Manager V2</span>
          {user && <span className="who">{user.display_name}</span>}
        </header>
        <Outlet />
      </div>

      <nav className="bottom-nav" aria-label="Navigazione principale">
        {BOTTOM_NAV_PRIMARY.map(({ to, label, icon: LinkIcon }) => (
          <NavLink key={to} to={to} className={navLinkClass}>
            <LinkIcon />
            <span>{label}</span>
          </NavLink>
        ))}
        <button type="button" onClick={() => setMoreOpen(true)} aria-label="Altre pagine">
          <MoreIcon />
          <span>Altro</span>
        </button>
      </nav>

      {moreOpen && (
        <>
          <div className="more-backdrop" onClick={() => setMoreOpen(false)} />
          <aside className="more-menu" aria-label="Altre pagine">
            <div className="more-menu-title">
              <span>Altro</span>
              <button type="button" className="more-menu-close" onClick={() => setMoreOpen(false)} aria-label="Chiudi">
                <XIcon width={16} height={16} />
              </button>
            </div>
            {BOTTOM_NAV_OVERFLOW.map(({ to, label, icon: LinkIcon }) => (
              <NavLink key={to} to={to} onClick={() => setMoreOpen(false)}>
                <LinkIcon />
                {label}
              </NavLink>
            ))}
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
