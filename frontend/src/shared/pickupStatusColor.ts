// V1 (DEFAULT_CALENDAR_TYPE_COLORS, app.py:85 + calendar_appt_card_html,
// app.py:9818): non e' una palette CSS statica per stato - e' un colore
// hex per tipo/stato, applicato via style inline su ogni card
// (background:{hex}22;color:{hex}, testo MAIUSCOLO). Qui riprodotto 1:1
// (stessi valori default, nessun colore personalizzato per utente: quello
// e' un gap, vedi Impostazioni Calendario in V1).
export const PICKUP_STATUS_HEX: Record<string, string> = {
  da_confermare: "#fde047",
  da_ritirare: "#fb7185",
  ritirato: "#4ade80",
  annullato: "#94a3b8",
};

export function pickupStatusStyle(status: string): { background: string; color: string } {
  const hex = PICKUP_STATUS_HEX[status] ?? "#94a3b8";
  return { background: `${hex}22`, color: hex };
}
