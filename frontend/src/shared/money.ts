/**
 * Fase 8 (doc12): unica fonte per la conversione centesimi<->euro,
 * duplicata in modo divergente (due formati diversi) in almeno 8 file
 * prima di questa estrazione - "mai due modi di dire la stessa cosa",
 * stesso principio gia' applicato lato backend (doc06 'Denaro').
 */

export function formatMoney(cents: number): string {
  return (cents / 100).toLocaleString("it-IT", { style: "currency", currency: "EUR" });
}

export function centsToEuroString(cents: number): string {
  return (cents / 100).toFixed(2);
}

/**
 * Se la stringa contiene una virgola, i punti che la precedono sono
 * separatori delle migliaia (stessa convenzione it-IT usata da
 * formatMoney, es. "1.234,56") e vanno rimossi prima di interpretare la
 * virgola come separatore decimale. Senza virgola, un punto resta
 * l'unico separatore decimale possibile (es. "120.50", gia' accettato
 * prima di questa distinzione).
 */
function normalizeEuroString(value: string): string {
  return value.includes(",") ? value.replace(/\./g, "").replace(",", ".") : value;
}

export function euroStringToCents(value: string): number {
  return Math.round(Number(normalizeEuroString(value)) * 100);
}

export function isValidEuroString(value: string): boolean {
  return value.trim().length > 0 && !Number.isNaN(Number(normalizeEuroString(value)));
}
