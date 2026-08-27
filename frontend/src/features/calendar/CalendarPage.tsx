import { Link, useSearchParams } from "react-router-dom";

import { useDeliveries } from "@/features/deliveries/api";
import { usePickups } from "@/features/pickups/api";

import { addDays, dayBounds, todayIso } from "./calendarDate";

const PICKUP_STATUS_LABELS: Record<string, string> = {
  da_confermare: "Da confermare",
  da_ritirare: "Da ritirare",
  ritirato: "Ritirato",
  annullato: "Annullato",
};

export function CalendarPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const date = searchParams.get("data") || todayIso();

  function setDate(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("data", value);
      return next;
    });
  }

  const { dateFrom, dateTo } = dayBounds(date);
  const { data: pickups, isLoading: pickupsLoading, isError: pickupsError } = usePickups({ dateFrom, dateTo });
  const { data: deliveries, isLoading: deliveriesLoading, isError: deliveriesError } = useDeliveries({ dateFrom, dateTo });

  const sortedPickups = [...(pickups ?? [])].sort((a, b) => a.start_at.localeCompare(b.start_at));
  const sortedDeliveries = [...(deliveries ?? [])].sort((a, b) => a.start_at.localeCompare(b.start_at));

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Calendario operativo</h1>
      </div>

      <div className="field-row">
        <button className="btn-ghost" onClick={() => setDate(addDays(date, -1))}>
          Giorno precedente
        </button>
        <button className="btn-ghost" onClick={() => setDate(todayIso())}>
          Oggi
        </button>
        <button className="btn-ghost" onClick={() => setDate(addDays(date, 1))}>
          Giorno successivo
        </button>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </div>

      <div className="section">
        <h2>Ritiri</h2>
        {pickupsLoading && <p className="loading">Caricamento...</p>}
        {pickupsError && <p className="error-banner">Errore nel caricamento dei ritiri.</p>}
        {sortedPickups.length === 0 && !pickupsLoading && <p className="empty-state">Nessun ritiro in questo giorno.</p>}
        {sortedPickups.length > 0 && (
          <div className="timeline">
            {sortedPickups.map((pickup) => (
              <div className="event" key={pickup.id}>
                <Link to={`/ritiri/${pickup.id}`}>
                  <b>{new Date(pickup.start_at).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}</b>{" "}
                  · {pickup.pickup_type}
                </Link>{" "}
                <span className={`badge status-${pickup.pickup_status}`}>{PICKUP_STATUS_LABELS[pickup.pickup_status]}</span>
                <br />
                <small className="sub">{pickup.animals.map((a) => a.name ?? "-").join(", ") || "Nessun animale"}</small>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="section">
        <h2>Riconsegne</h2>
        {deliveriesLoading && <p className="loading">Caricamento...</p>}
        {deliveriesError && <p className="error-banner">Errore nel caricamento delle riconsegne.</p>}
        {sortedDeliveries.length === 0 && !deliveriesLoading && (
          <p className="empty-state">Nessuna riconsegna in questo giorno.</p>
        )}
        {sortedDeliveries.length > 0 && (
          <div className="timeline">
            {sortedDeliveries.map((delivery) => (
              <div className="event" key={delivery.id}>
                <Link to={`/riconsegne/${delivery.id}`}>
                  <b>{new Date(delivery.start_at).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}</b>{" "}
                  · {delivery.delivery_type}
                </Link>
                <br />
                <small className="sub">
                  {delivery.linked_practice_id ? `Pratica #${delivery.linked_practice_id}` : "Nessuna pratica collegata"}
                </small>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
