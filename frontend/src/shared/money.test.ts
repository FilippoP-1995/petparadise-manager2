import { describe, expect, it } from "vitest";

import { centsToEuroString, euroStringToCents, formatMoney, isValidEuroString } from "./money";

describe("formatMoney", () => {
  // Confronto via regex, non un literal esatto: Intl.NumberFormat per
  // it-IT/EUR inserisce uno spazio unificatore (U+00A0) prima del
  // simbolo, non uno spazio normale - un confronto esatto con una
  // stringa letterale renderebbe il test fragile su un dettaglio di
  // rendering invisibile, non sul comportamento reale della funzione.
  it("formats zero", () => {
    expect(formatMoney(0)).toMatch(/^0,00\s€$/);
  });

  it("formats a whole-euro amount", () => {
    expect(formatMoney(100)).toMatch(/^1,00\s€$/);
  });

  it("formats the smallest amount (1 cent)", () => {
    expect(formatMoney(1)).toMatch(/^0,01\s€$/);
  });

  it("formats an amount with 2 decimals", () => {
    expect(formatMoney(12050)).toMatch(/^120,50\s€$/);
  });

  it("formats negative cents (residuo/sovrapagamento)", () => {
    expect(formatMoney(-500)).toContain("5,00");
  });

  it("groups thousands for a large amount (999999,99)", () => {
    // La formattazione it-IT applica il separatore delle migliaia solo da
    // 5 cifre intere in su (regola CLDR "min2", verificata con Node:
    // 1234,56 NON raggruppa, 12345,67 si') - non e' un dettaglio inventato.
    expect(formatMoney(99999999)).toMatch(/^999\.999,99\s€$/);
  });

  it("groups thousands exactly at the 10.000 threshold", () => {
    expect(formatMoney(1000000)).toMatch(/^10\.000,00\s€$/);
  });

  it("does not group a 4-digit integer part", () => {
    expect(formatMoney(123456)).toMatch(/^1234,56\s€$/);
  });
});

describe("euroStringToCents", () => {
  it("converts a whole euro amount", () => {
    expect(euroStringToCents("1")).toBe(100);
  });

  it("converts an amount with 1 decimal digit", () => {
    expect(euroStringToCents("120,5")).toBe(12050);
  });

  it("converts an amount with 2 decimal digits", () => {
    expect(euroStringToCents("120,50")).toBe(12050);
  });

  it("converts the smallest amount (0,01)", () => {
    expect(euroStringToCents("0,01")).toBe(1);
  });

  it("converts a large amount without a thousands separator", () => {
    expect(euroStringToCents("999999,99")).toBe(99999999);
  });

  it("strips the thousands separator when a decimal comma is present", () => {
    // Bug reale: prima della correzione, "1.234,56" (lo stesso formato
    // prodotto da formatMoney per importi >= 10.000) produceva NaN perche'
    // il punto delle migliaia veniva scambiato per un separatore
    // decimale invece di essere rimosso.
    expect(euroStringToCents("1.234,56")).toBe(123456);
  });

  it("strips multiple thousands separators", () => {
    expect(euroStringToCents("1.234.567,89")).toBe(123456789);
  });

  it("accepts a dot as decimal separator when no comma is present", () => {
    expect(euroStringToCents("120.50")).toBe(12050);
  });

  it("treats an empty string as zero, not NaN", () => {
    expect(euroStringToCents("")).toBe(0);
  });

  it("returns NaN for non-numeric text", () => {
    expect(Number.isNaN(euroStringToCents("abc"))).toBe(true);
  });

  it("rounds fractional cents from floating point", () => {
    expect(euroStringToCents("0,1")).toBe(10);
  });
});

describe("centsToEuroString / euroStringToCents round-trip", () => {
  it("round-trips a whole euro amount", () => {
    expect(euroStringToCents(centsToEuroString(34000))).toBe(34000);
  });

  it("round-trips an amount above the grouping threshold", () => {
    expect(euroStringToCents(centsToEuroString(1234567))).toBe(1234567);
  });
});

describe("isValidEuroString", () => {
  it("accepts a valid amount with a comma decimal separator", () => {
    expect(isValidEuroString("340,00")).toBe(true);
  });

  it("accepts a valid amount with a thousands separator", () => {
    expect(isValidEuroString("1.234,56")).toBe(true);
  });

  it("rejects an empty string", () => {
    expect(isValidEuroString("")).toBe(false);
  });

  it("rejects a whitespace-only string", () => {
    expect(isValidEuroString("   ")).toBe(false);
  });

  it("rejects non-numeric text", () => {
    expect(isValidEuroString("abc")).toBe(false);
  });
});
