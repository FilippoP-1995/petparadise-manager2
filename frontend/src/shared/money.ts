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

export function euroStringToCents(value: string): number {
  return Math.round(Number(value.replace(",", ".")) * 100);
}

export function isValidEuroString(value: string): boolean {
  const normalized = value.replace(",", ".");
  return value.trim().length > 0 && !Number.isNaN(Number(normalized));
}
