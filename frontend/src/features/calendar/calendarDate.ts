/**
 * Data locale in formato YYYY-MM-DD, calcolata SENZA passare da
 * toISOString() (che normalizza a UTC): con un fuso orario in anticipo
 * su UTC (es. Europe/Rome), il giro UTC tronca la data allo stesso
 * giorno o a due giorni indietro invece di uno - bug reale riprodotto in
 * browser, non un'ipotesi (Giorno successivo restava fermo, Giorno
 * precedente saltava di due giorni).
 */
export function toLocalDateIso(d: Date): string {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function todayIso(): string {
  return toLocalDateIso(new Date());
}

export function addDays(dateIso: string, days: number): string {
  const d = new Date(`${dateIso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return toLocalDateIso(d);
}

/**
 * Confini del giorno locale espressi come istanti UTC assoluti (con
 * offset esplicito, via toISOString()) - qui il passaggio per UTC e'
 * corretto e voluto: il backend confronta date_from/date_to con
 * start_at (TIMESTAMPTZ), quindi l'istante assoluto di inizio/fine del
 * giorno locale e' esattamente cio' che serve, non la data-stringa.
 */
export function dayBounds(dateIso: string): { dateFrom: string; dateTo: string } {
  const start = new Date(`${dateIso}T00:00:00`);
  const end = new Date(`${dateIso}T00:00:00`);
  end.setDate(end.getDate() + 1);
  return { dateFrom: start.toISOString(), dateTo: end.toISOString() };
}
