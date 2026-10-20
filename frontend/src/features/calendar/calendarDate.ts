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

/** Lunedi'=0 ... Domenica=6 (V1: la settimana del calendario parte sempre di lunedi'). */
export function weekdayMonFirst(dateIso: string): number {
  const d = new Date(`${dateIso}T00:00:00`);
  return (d.getDay() + 6) % 7;
}

export function weekStart(dateIso: string): string {
  return addDays(dateIso, -weekdayMonFirst(dateIso));
}

/** I 7 giorni (lun->dom) della settimana che contiene dateIso - stessa finestra
 * usata da V1 sia per la vista Giorno che per la vista Settimana (la daybar
 * mostra sempre l'intera settimana corrente, non solo il giorno selezionato). */
export function weekDays(dateIso: string): string[] {
  const start = weekStart(dateIso);
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

export function weekRangeBounds(dateIso: string): { dateFrom: string; dateTo: string } {
  const days = weekDays(dateIso);
  return { dateFrom: dayBounds(days[0]).dateFrom, dateTo: dayBounds(days[6]).dateTo };
}

/** Griglia di 42 celle (6 settimane, lun->dom) per la vista Mese - stessa
 * costruzione di V1 (month_start.weekday() come offset, sempre 42 celle
 * cosi' la griglia ha sempre altezza fissa indipendentemente dal mese). */
export function monthGridDays(dateIso: string): string[] {
  const d = new Date(`${dateIso}T00:00:00`);
  const monthFirstIso = toLocalDateIso(new Date(d.getFullYear(), d.getMonth(), 1));
  const start = weekStart(monthFirstIso);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

export function monthRangeBounds(dateIso: string): { dateFrom: string; dateTo: string } {
  const days = monthGridDays(dateIso);
  return { dateFrom: dayBounds(days[0]).dateFrom, dateTo: dayBounds(days[41]).dateTo };
}

export function isSameMonth(dateIso: string, referenceIso: string): boolean {
  return dateIso.slice(0, 7) === referenceIso.slice(0, 7);
}

export function addMonths(dateIso: string, months: number): string {
  const d = new Date(`${dateIso}T00:00:00`);
  const day = d.getDate();
  d.setDate(1);
  d.setMonth(d.getMonth() + months);
  const lastDayOfTargetMonth = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
  d.setDate(Math.min(day, lastDayOfTargetMonth));
  return toLocalDateIso(d);
}
