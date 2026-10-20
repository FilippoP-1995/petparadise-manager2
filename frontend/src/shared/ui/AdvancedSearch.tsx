import type { ReactNode } from "react";

// V1 (collapse_advanced_search, app.py): ogni form di ricerca GET con 2 o
// più controlli (input/select/textarea) viene automaticamente racchiuso
// in un <details class="advanced-search"><summary>Ricerca avanzata</summary>,
// collassato di default - un form con un solo campo (semplice ricerca
// testuale) resta invece sempre visibile, senza collapse. Qui replicato
// come componente esplicito invece di una trasformazione automatica sulla
// stringa HTML (non applicabile in React), applicato solo alle pagine che
// hanno realmente 2+ controlli di filtro - non ovunque.
export function AdvancedSearch({ children, defaultOpen }: { children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details className="advanced-search" open={defaultOpen}>
      <summary>Ricerca avanzata</summary>
      <div className="advanced-search-form">{children}</div>
    </details>
  );
}
