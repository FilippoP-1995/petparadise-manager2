import { useState, type ReactNode, type SVGProps } from "react";

type IconComponent = (props: SVGProps<SVGSVGElement>) => JSX.Element;
type TapColor = "pink" | "blue" | "green" | "orange" | "purple";
type SectionTone = TapColor | "gray";

// V1 (calendar_event_form, app.py:10930+): ogni campo del wizard "Nuovo
// evento" e' un ".calendar-tap-card" - un chip icona colorato a sinistra +
// label/valore a destra, non un .field standard. Riusato identico per
// Pickup/Delivery in modo che le due form condividano lo stesso codice
// grafico invece di duplicarlo.
export function TapCard({ icon: TapIcon, color, label, children }: { icon: IconComponent; color: TapColor; label: string; children: ReactNode }) {
  return (
    <div className="calendar-tap-card">
      <span className={`calendar-tap-card-icon calendar-icon-${color}`}>
        <TapIcon />
      </span>
      <div className="calendar-tap-card-body">
        <small>{label}</small>
        {children}
      </div>
    </div>
  );
}

// V1 (".calendar-v2-card"/".calendar-subblock"): la card "vetro" che
// raggruppa piu' TapCard sotto un eyebrow in maiuscolo.
export function CalendarCard({ eyebrow, children }: { eyebrow: string; children: ReactNode }) {
  return (
    <section className="calendar-v2-card">
      <h2 className="calendar-v2-eyebrow">{eyebrow}</h2>
      {children}
    </section>
  );
}

// V1 (".section.collapsible.tone-*", app.py:10982-10985): sezioni apri/
// chiudi colorate (Cliente=viola, Animali=arancio, Note=grigio). Il
// contenuto resta montato e viene solo nascosto via CSS quando chiuso -
// stesso meccanismo di V1 (classe "collapsed" su .section), cosi' i campi
// react-hook-form al suo interno non perdono valore/registrazione quando
// la sezione si chiude.
export function CollapsibleCalendarSection({
  icon: SectionIcon,
  color,
  title,
  defaultOpen = false,
  children,
}: {
  icon: IconComponent;
  color: SectionTone;
  title: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`section collapsible tone-${color}${open ? "" : " collapsed"}`}>
      <h2 onClick={() => setOpen((o) => !o)}>
        <span className={`calendar-tap-card-icon${color === "gray" ? "" : ` calendar-icon-${color}`}`}>
          <SectionIcon />
        </span>
        {title}
      </h2>
      {children}
    </section>
  );
}
