import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ArchiveIcon, BellIcon, HomeIcon, PawIcon, TruckIcon } from "@/app/icons";
import { PickupEventForm } from "@/features/pickups/PickupFormPage";
import { DeliveryEventForm } from "@/features/deliveries/DeliveryFormPage";

type EventKind = "Ritiro" | "Ritiro in sede" | "Riconsegna" | "Riconsegna in sede" | "Appuntamento";

// V1 (calendar_event_form, app.py:10884): stesse 5 card, stesso ordine,
// stessa etichetta/descrizione/icona/colore - lette alla lettera dal
// dizionario reale, non ricostruite a memoria.
const TYPE_OPTIONS: { kind: EventKind; label: string; desc: string; icon: typeof PawIcon; colorClass: string }[] = [
  { kind: "Ritiro", label: "RITIRO", desc: "Ritiro presso veterinario o privato", icon: PawIcon, colorClass: "calendar-icon-pink" },
  { kind: "Ritiro in sede", label: "RITIRO IN SEDE", desc: "Consegna presso una sede", icon: HomeIcon, colorClass: "calendar-icon-blue" },
  { kind: "Riconsegna", label: "RICONSEGNA", desc: "Riconsegna al cliente", icon: ArchiveIcon, colorClass: "calendar-icon-green" },
  { kind: "Riconsegna in sede", label: "RICONSEGNA IN SEDE", desc: "Ritiro ceneri presso sede", icon: TruckIcon, colorClass: "calendar-icon-orange" },
  { kind: "Appuntamento", label: "PROMEMORIA", desc: "Promemoria, riunioni, fornitori e impegni", icon: BellIcon, colorClass: "calendar-icon-purple" },
];

// V1 (app.py:10835-11009, class reale "calendar-form-v2" sul <main>):
// titlebar "Nuovo evento" + chiusura "x", poi "Che tipo di evento vuoi
// creare?" con la griglia a 5 card, poi sotto - senza navigare altrove -
// i campi del tipo scelto. V1 e' un unico form multi-tipo (Ritiro/Ritiro in
// sede/Riconsegna/Riconsegna in sede/Appuntamento); V2 ha domini distinti
// Pickup/Delivery gia' costruiti (PickupEventForm/DeliveryEventForm,
// riusati qui identici, presettando solo il "luogo" - domicilio per la
// card semplice, sede aziendale per quella "in sede"). "Appuntamento"
// (Promemoria) in V1 crea un calendar_event generico: V2 non ha alcun
// dominio "promemoria/appuntamento" (nessuna tabella, nessuno schema) -
// gap di dominio disclosed, mostrato come tale invece di un form finto.
export function CalendarEventFormPage() {
  const navigate = useNavigate();
  const [selected, setSelected] = useState<EventKind | null>(null);

  function close() {
    navigate("/calendario");
  }

  return (
    <main className="wrap calendar-form calendar-form-v2">
      <div className="titlebar calendar-v2-topbar">
        <div>
          <h1>Nuovo evento</h1>
          <p className="sub">Scegli il tipo di evento e compila i dati.</p>
        </div>
        <button type="button" className="btn ghost calendar-v2-close" onClick={close} aria-label="Chiudi">
          ×
        </button>
      </div>

      <section className="section">
        <h2>Che tipo di evento vuoi creare?</h2>
        <p className="sub">Scegli l'operazione da registrare.</p>
        <div className="calendar-type-grid">
          {TYPE_OPTIONS.map(({ kind, label, desc, icon: OptionIcon, colorClass }) => (
            <label className="calendar-type-option" key={kind}>
              <input type="radio" name="event_type" value={kind} checked={selected === kind} onChange={() => setSelected(kind)} required />
              <span className={`calendar-event-type-icon ${colorClass}`}>
                <OptionIcon />
              </span>
              <span>
                <b>{label}</b>
                <small className="sub">{desc}</small>
              </span>
            </label>
          ))}
        </div>
      </section>

      {selected === "Ritiro" && (
        <>
          <div style={{ height: 14 }} />
          <PickupEventForm defaultPickupType="domicilio" onCreated={() => navigate("/ritiri")} onCancel={close} />
        </>
      )}
      {selected === "Ritiro in sede" && (
        <>
          <div style={{ height: 14 }} />
          <PickupEventForm defaultPickupType="sede_aziendale" onCreated={() => navigate("/ritiri")} onCancel={close} />
        </>
      )}
      {selected === "Riconsegna" && (
        <>
          <div style={{ height: 14 }} />
          <DeliveryEventForm defaultDeliveryType="domicilio" onCreated={() => navigate("/riconsegne")} onCancel={close} />
        </>
      )}
      {selected === "Riconsegna in sede" && (
        <>
          <div style={{ height: 14 }} />
          <DeliveryEventForm defaultDeliveryType="sede_aziendale" onCreated={() => navigate("/riconsegne")} onCancel={close} />
        </>
      )}
      {selected === "Appuntamento" && (
        <>
          <div style={{ height: 14 }} />
          <div className="section">
            <h2>Promemoria</h2>
            <p className="sub">
              Questo tipo di evento non e' ancora disponibile in V2: non esiste un dominio "appuntamento/promemoria" nel motore
              (nessuna tabella, nessuno schema dedicato). Per non inventare dati o comportamenti non reali, questa sezione resta
              vuota fino a quando il dominio non sara' costruito.
            </p>
          </div>
        </>
      )}
    </main>
  );
}
