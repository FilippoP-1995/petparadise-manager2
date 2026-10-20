// V1 (CREMATION_STATUS_LABELS, app.py:7266-7269): stessa etichetta
// MAIUSCOLA e stessa classe dedicata (mai la palette generica
// .badge.status-*, che per questi 3 stati darebbe colori sbagliati).
export const CREMATION_STATUS: Record<string, { label: string; cls: string }> = {
  pianificato: { label: "PIANIFICATO", cls: "cremation-status-planned" },
  in_attesa: { label: "IN ATTESA", cls: "cremation-status-waiting" },
  completato: { label: "COMPLETATO", cls: "cremation-status-done" },
};
