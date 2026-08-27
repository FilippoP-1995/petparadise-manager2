import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { addDays, dayBounds, toLocalDateIso, todayIso } from "./calendarDate";

// Niente @types/node nel progetto (nessuna dipendenza nuova aggiunta solo
// per questo test): dichiarazione locale minima per il solo accesso a
// process.env.TZ, che qui serve a fissare deterministicamente il fuso
// orario del processo di test.
declare const process: { env: Record<string, string | undefined> };

/**
 * Bug reale riprodotto in browser (non un'ipotesi): con il fuso orario
 * del processo avanti su UTC (es. Europe/Rome, UTC+2 in estate),
 * l'implementazione precedente calcolava addDays/todayIso passando da
 * `new Date(...).toISOString().slice(0, 10)` - il giro per UTC tronca
 * la mezzanotte locale nel giorno sbagliato, cosi' "Giorno successivo"
 * restava fermo sullo stesso giorno e "Giorno precedente" saltava di
 * due giorni invece di uno. Questi test fissano il fuso del processo
 * prima di ogni caso per riprodurre esattamente quel bug in modo
 * deterministico, indipendentemente dal fuso della macchina che esegue
 * la suite.
 */

const ORIGINAL_TZ = process.env.TZ;

function withTimeZone(tz: string, run: () => void) {
  process.env.TZ = tz;
  try {
    run();
  } finally {
    process.env.TZ = ORIGINAL_TZ;
  }
}

describe("addDays - fuso orario avanti su UTC (Europe/Rome)", () => {
  beforeEach(() => {
    process.env.TZ = "Europe/Rome";
  });
  afterEach(() => {
    process.env.TZ = ORIGINAL_TZ;
  });

  it("avanza di un giorno (Giorno successivo)", () => {
    expect(addDays("2027-05-10", 1)).toBe("2027-05-11");
  });

  it("torna indietro di un giorno (Giorno precedente)", () => {
    expect(addDays("2027-05-10", -1)).toBe("2027-05-09");
  });

  it("attraversa il cambio di mese in avanti", () => {
    expect(addDays("2027-05-31", 1)).toBe("2027-06-01");
  });

  it("attraversa il cambio di mese all'indietro", () => {
    expect(addDays("2027-06-01", -1)).toBe("2027-05-31");
  });

  it("attraversa il cambio dell'ora legale (ultima domenica di marzo)", () => {
    expect(addDays("2027-03-27", 1)).toBe("2027-03-28");
  });
});

describe("addDays - fuso orario indietro rispetto a UTC (America/New_York)", () => {
  beforeEach(() => {
    process.env.TZ = "America/New_York";
  });
  afterEach(() => {
    process.env.TZ = ORIGINAL_TZ;
  });

  it("avanza di un giorno", () => {
    expect(addDays("2027-05-10", 1)).toBe("2027-05-11");
  });

  it("torna indietro di un giorno", () => {
    expect(addDays("2027-05-10", -1)).toBe("2027-05-09");
  });
});

describe("addDays - UTC (offset zero, il caso in cui il bug era invisibile)", () => {
  beforeEach(() => {
    process.env.TZ = "UTC";
  });
  afterEach(() => {
    process.env.TZ = ORIGINAL_TZ;
  });

  it("avanza di un giorno", () => {
    expect(addDays("2027-05-10", 1)).toBe("2027-05-11");
  });
});

describe("todayIso", () => {
  it("restituisce la data locale, non quella UTC, vicino alla mezzanotte", () => {
    withTimeZone("Europe/Rome", () => {
      // 00:30 locale in Europe/Rome (UTC+2) e' ancora il giorno precedente
      // in UTC - todayIso deve seguire il calendario locale.
      const localMidnightThirty = new Date("2027-05-10T00:30:00");
      expect(toLocalDateIso(localMidnightThirty)).toBe("2027-05-10");
    });
  });

  it("e' coerente con il calendario del sistema al momento della chiamata", () => {
    const now = new Date();
    expect(todayIso()).toBe(toLocalDateIso(now));
  });
});

describe("dayBounds", () => {
  it("produce istanti UTC assoluti che corrispondono alla mezzanotte locale", () => {
    withTimeZone("Europe/Rome", () => {
      const { dateFrom, dateTo } = dayBounds("2027-05-10");
      // Mezzanotte locale in CEST (UTC+2) = 22:00 UTC del giorno prima.
      expect(dateFrom).toBe("2027-05-09T22:00:00.000Z");
      expect(dateTo).toBe("2027-05-10T22:00:00.000Z");
    });
  });

  it("copre esattamente 24 ore", () => {
    withTimeZone("Europe/Rome", () => {
      const { dateFrom, dateTo } = dayBounds("2027-05-10");
      const diffMs = new Date(dateTo).getTime() - new Date(dateFrom).getTime();
      expect(diffMs).toBe(24 * 60 * 60 * 1000);
    });
  });
});
