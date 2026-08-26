import { useState } from "react";

import { usePractices, type Practice } from "@/features/practices/api";

/**
 * Ricerca-e-seleziona pratica, stesso pattern di ClientPicker - riusato
 * dal form Nuova Fattura per collegare una pratica reale.
 */
export function PracticePicker({
  selectedPractice,
  onSelect,
}: {
  selectedPractice: Practice | null;
  onSelect: (practice: Practice | null) => void;
}) {
  const [search, setSearch] = useState("");
  const { data: results } = usePractices({ q: search });

  if (selectedPractice) {
    return (
      <p>
        {selectedPractice.practice_number}{" "}
        <button type="button" className="btn-ghost" onClick={() => onSelect(null)}>
          Cambia
        </button>
      </p>
    );
  }

  return (
    <>
      <input placeholder="Cerca pratica per numero o cliente..." value={search} onChange={(e) => setSearch(e.target.value)} />
      {results && search && (
        <ul className="reminders-todo-list">
          {results.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className="reminders-todo-row"
                onClick={() => {
                  onSelect(p);
                  setSearch("");
                }}
              >
                {p.practice_number}
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
