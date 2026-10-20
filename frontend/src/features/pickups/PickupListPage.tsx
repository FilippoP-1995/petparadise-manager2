import { Link, useSearchParams } from "react-router-dom";

import { PICKUP_STATUS_HEX, pickupStatusStyle } from "@/shared/pickupStatusColor";
import { AdvancedSearch } from "@/shared/ui/AdvancedSearch";
import { CalendarApptCard } from "@/shared/ui/CalendarApptCard";
import { useListQueryParams } from "@/shared/useListQueryParams";

import { usePickups } from "./api";

const STATUS_LABELS: Record<string, string> = {
  da_confermare: "Da confermare",
  da_ritirare: "Da ritirare",
  ritirato: "Ritirato",
  annullato: "Annullato",
};

// V1 non ha un pickup_type isolato (e' testo libero nel titolo evento) -
// qui i valori dell'enum PickupType sono mostrati con la stessa etichetta
// italiana gia' usata da PickupFormPage.tsx, non con lo slug grezzo.
const PICKUP_TYPE_LABELS: Record<string, string> = {
  sede_aziendale: "Sede aziendale",
  domicilio: "Domicilio",
  veterinario: "Veterinario",
  collaboratore: "Collaboratore",
  altro: "Altro",
};

export function PickupListPage() {
  const { q, offset, setSearch, setOffset } = useListQueryParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const status = searchParams.get("status") ?? "";

  function setStatus(value: string) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("status", value);
        else next.delete("status");
        next.delete("offset");
        return next;
      },
      { replace: true },
    );
  }

  const { data: pickups, isLoading, isError } = usePickups({ q, status: status || undefined, offset });

  return (
    <main className="wrap">
      <div className="titlebar">
        <h1>Ritiri</h1>
        <Link className="btn" to="/ritiri/nuovo">
          + Nuovo ritiro
        </Link>
      </div>

      <AdvancedSearch defaultOpen={Boolean(q || status)}>
        <input
          className="search-input"
          placeholder="Cerca per cliente o note..."
          defaultValue={q}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Tutti gli stati</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </AdvancedSearch>

      {isLoading && <p className="loading">Caricamento...</p>}
      {isError && <p className="error-banner">Errore nel caricamento dei ritiri.</p>}
      {pickups && pickups.length === 0 && <p className="empty-state">Nessun ritiro trovato.</p>}

      {pickups && pickups.length > 0 && (
        <div className="calendar-appt-list">
          {pickups.map((pickup) => {
            const animalNames = pickup.animals.map((a) => a.name ?? "-").join(", ") || "Nessun animale";
            const ownerLine = pickup.client_name
              ? `${pickup.client_name}${pickup.client_phone ? " · " + pickup.client_phone : ""}`
              : undefined;
            return (
              <CalendarApptCard
                key={pickup.id}
                href={`/ritiri/${pickup.id}`}
                kind="pickup"
                borderColorHex={PICKUP_STATUS_HEX[pickup.pickup_status] ?? "#94a3b8"}
                timeText={new Date(pickup.start_at).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" })}
                title={PICKUP_TYPE_LABELS[pickup.pickup_type] ?? pickup.pickup_type}
                nameLine={`🐾 ${animalNames} · ${pickup.linked_practice_id ? `Pratica #${pickup.linked_practice_id}` : "Nessuna pratica collegata"}`}
                ownerLine={ownerLine}
                locationLine={pickup.pickup_contact_name || pickup.pickup_address || undefined}
                statusText={STATUS_LABELS[pickup.pickup_status].toUpperCase()}
                statusStyle={pickupStatusStyle(pickup.pickup_status)}
                address={pickup.pickup_address}
              />
            );
          })}
        </div>
      )}

      <div className="pagination">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
          Precedenti
        </button>
        <button disabled={!pickups || pickups.length < 50} onClick={() => setOffset(offset + 50)}>
          Successivi
        </button>
      </div>
    </main>
  );
}
