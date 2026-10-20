import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { CheckCircleIcon, NavigationIcon, WalletIcon } from "@/app/icons";

// V1 (calendar_appt_card_html, app.py:9840-9852): stessa gerarchia reale -
// time-col (pallino colorato + ora), tipo in alto, titolo MAIUSCOLO, nome
// animale, proprietario, luogo, pagamento inline, riga finale
// stato+azioni. Condivisa da Ritiri/Riconsegne/Calendario cosi' le tre
// viste restano identiche come in V1 ("Le card devono essere praticamente
// identiche alla vista Giorno").
export function CalendarApptCard({
  href,
  kind,
  borderColorHex,
  timeText,
  endTimeText,
  title,
  nameLine,
  ownerLine,
  locationLine,
  paymentText,
  paymentPaid,
  statusText,
  statusStyle,
  address,
}: {
  href: string;
  kind: "pickup" | "delivery";
  borderColorHex: string;
  timeText: string;
  endTimeText?: string;
  title: ReactNode;
  nameLine?: string;
  ownerLine?: string;
  locationLine?: string;
  paymentText?: string | null;
  paymentPaid?: boolean;
  statusText?: string;
  statusStyle?: { background: string; color: string };
  address?: string | null;
}) {
  const navigate = useNavigate();
  // V1 (calendar_appt_card_html, app.py:9840): l'intera card e' un
  // <article onclick="location.href=...">, non un <a> - i pulsanti azione
  // dentro (naviga) sono <a> reali, e un <a> non puo' contenere un altro
  // <a> (HTML non valido, causava un warning React reale).
  return (
    <div
      className={`calendar-appt-card calendar-appt-type-${kind}`}
      style={{ borderLeftColor: borderColorHex }}
      role="link"
      tabIndex={0}
      onClick={() => navigate(href)}
      onKeyDown={(e) => {
        if (e.key === "Enter") navigate(href);
      }}
    >
      <div className="calendar-appt-time-col">
        <span className="calendar-appt-time-dot" style={{ background: borderColorHex }} />
        <span className="calendar-appt-time">{timeText}</span>
        {endTimeText && <span className="calendar-appt-time-end">→ {endTimeText}</span>}
      </div>
      <div className="calendar-appt-main">
        <div className="calendar-appt-top">
          <span className="calendar-appt-type-label">{kind === "pickup" ? "RITIRO" : "RICONSEGNA"}</span>
        </div>
        <div className="calendar-appt-title">{title}</div>
        {nameLine && <div className="calendar-appt-name">{nameLine}</div>}
        {ownerLine && <div className="calendar-appt-owner">{ownerLine}</div>}
        {locationLine && <div className="calendar-appt-location">{locationLine}</div>}
        {paymentText && (
          <div className={`calendar-appt-payment ${paymentPaid ? "calendar-appt-payment-paid" : "calendar-appt-payment-due"}`}>
            {paymentPaid ? <CheckCircleIcon /> : <WalletIcon />}
            <span>{paymentText}</span>
          </div>
        )}
        <div className="calendar-appt-bottom">
          {statusText && statusStyle && (
            <span className="calendar-appt-status" style={statusStyle}>
              {statusText}
            </span>
          )}
          <div className="calendar-appt-actions">
            {address && (
              <a
                className="calendar-appt-action"
                href={`https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(address)}`}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Naviga"
                onClick={(e) => e.stopPropagation()}
              >
                <NavigationIcon />
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
