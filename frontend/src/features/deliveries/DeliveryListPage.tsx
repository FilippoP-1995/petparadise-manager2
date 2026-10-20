import { Link } from "react-router-dom";

import { formatMoney } from "@/shared/money";
import { AdvancedSearch } from "@/shared/ui/AdvancedSearch";
import { CalendarApptCard } from "@/shared/ui/CalendarApptCard";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { useDeliveries } from "./api";

// V1 non ha un delivery_type isolato (e' testo libero nel titolo evento) -
// qui i valori dell'enum DeliveryType sono mostrati con la stessa etichetta
// italiana gia' usata da DeliveryFormPage.tsx, non con lo slug grezzo.
const DELIVERY_TYPE_LABELS: Record<string, string> = {
  sede_aziendale: "Sede aziendale",
  domicilio: "Domicilio",
  ambulatorio: "Ambulatorio",
  altro: "Altro",
};

export function DeliveryListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const { data: deliveries, isLoading, isError } = useDeliveries({ q, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Riconsegne</h1>
        <Link className="btn" to="/riconsegne/nuova">
          + Nuova riconsegna
        </Link>
      </div>

      <AdvancedSearch defaultOpen={Boolean(q)}>
        <input
          className="search-input"
          placeholder="Cerca..."
          defaultValue={q}
          onChange={(e) => setSearch(e.target.value)}
        />
      </AdvancedSearch>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento delle riconsegne.</p>}
      {deliveries && deliveries.length === 0 && <p className="empty-state">Nessuna riconsegna trovata.</p>}

      {deliveries && deliveries.length > 0 && (
        <div className="calendar-appt-list">
          {deliveries.map((delivery) => {
            // V1 (calendar_appt_card_html, app.py:9805-9809): verde se
            // "Pagato", giallo per ogni altro stato non vuoto.
            const paid = delivery.preliminary_payment_status === "Pagato";
            const paymentText = delivery.preliminary_payment_status
              ? `${delivery.preliminary_payment_status}${
                  delivery.preliminary_payment_amount != null ? ` · ${formatMoney(delivery.preliminary_payment_amount)}` : ""
                }`
              : null;
            const ownerLine = delivery.client_name
              ? `${delivery.client_name}${delivery.client_phone ? " · " + delivery.client_phone : ""}`
              : undefined;
            return (
              <CalendarApptCard
                key={delivery.id}
                href={`/riconsegne/${delivery.id}`}
                kind="delivery"
                borderColorHex="#60a5fa"
                timeText={new Date(delivery.start_at).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}
                title={DELIVERY_TYPE_LABELS[delivery.delivery_type] ?? delivery.delivery_type}
                nameLine={delivery.linked_practice_id ? `Pratica #${delivery.linked_practice_id}` : "Nessuna pratica collegata"}
                ownerLine={ownerLine}
                locationLine={delivery.delivery_address || undefined}
                paymentText={paymentText}
                paymentPaid={paid}
                address={delivery.delivery_address}
              />
            );
          })}
        </div>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!deliveries || deliveries.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
