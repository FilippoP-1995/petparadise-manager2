import { describe, expect, it } from "vitest";

import { centsToEuroString, euroStringToCents, formatMoney, isValidEuroString } from "./money";

describe("formatMoney", () => {
  // Confronto via regex, non un literal esatto: Intl.NumberFormat per
  // it-IT/EUR inserisce uno spazio unificatore (U+00A0) prima del
  // simbolo, non uno spazio normale - un confronto esatto con una
  // stringa letterale renderebbe il test fragile su un dettaglio di
  // rendering invisibile, non sul comportamento reale della funzione.
  it("formats cents as an Italian-locale euro string", () => {
    expect(formatMoney(34000)).toMatch(/^340,00\s€$/);
  });

  it("formats negative cents (residuo/sovrapagamento)", () => {
    expect(formatMoney(-500)).toContain("5,00");
  });

  it("formats zero", () => {
    expect(formatMoney(0)).toMatch(/^0,00\s€$/);
  });
});

describe("centsToEuroString / euroStringToCents round-trip", () => {
  it("round-trips a whole euro amount", () => {
    expect(euroStringToCents(centsToEuroString(34000))).toBe(34000);
  });

  it("accepts a comma decimal separator", () => {
    expect(euroStringToCents("120,50")).toBe(12050);
  });

  it("accepts a dot decimal separator", () => {
    expect(euroStringToCents("120.50")).toBe(12050);
  });

  it("rounds fractional cents from floating point", () => {
    expect(euroStringToCents("0,1")).toBe(10);
  });
});

describe("isValidEuroString", () => {
  it("accepts a valid amount", () => {
    expect(isValidEuroString("340,00")).toBe(true);
  });

  it("rejects an empty string", () => {
    expect(isValidEuroString("")).toBe(false);
  });

  it("rejects non-numeric text", () => {
    expect(isValidEuroString("abc")).toBe(false);
  });
});
