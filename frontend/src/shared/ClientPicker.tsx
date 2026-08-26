import { useState } from "react";

import { useClients, type Client } from "@/features/clients/api";

/**
 * Ricerca-e-seleziona cliente, riusato da ogni form che ha bisogno di
 * collegare un cliente reale (Pratica, Ritiro) - estratto qui invece di
 * duplicato, cosi' come richiesto esplicitamente ("il codice condiviso
 * deve rimanere condiviso", sezione 13).
 */
export function ClientPicker({
  selectedClient,
  onSelect,
}: {
  selectedClient: Client | null;
  onSelect: (client: Client | null) => void;
}) {
  const [search, setSearch] = useState("");
  const { data: results } = useClients({ q: search });

  if (selectedClient) {
    return (
      <p>
        {[selectedClient.first_name, selectedClient.last_name].filter(Boolean).join(" ") || selectedClient.company_name}{" "}
        <button type="button" className="btn-ghost" onClick={() => onSelect(null)}>
          Cambia
        </button>
      </p>
    );
  }

  return (
    <>
      <input placeholder="Cerca cliente per nome o telefono..." value={search} onChange={(e) => setSearch(e.target.value)} />
      {results && search && (
        <ul className="reminders-todo-list">
          {results.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="reminders-todo-row"
                onClick={() => {
                  onSelect(c);
                  setSearch("");
                }}
              >
                {[c.first_name, c.last_name].filter(Boolean).join(" ") || c.company_name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
