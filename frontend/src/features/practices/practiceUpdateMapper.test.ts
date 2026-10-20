import { describe, expect, it } from "vitest";

import { practiceToUpdateInput } from "./practiceUpdateMapper";
import type { Practice } from "./api";

// V1 (practice_autosave, app.py:17344-17362): l'anomalia storica nasceva
// proprio da un editing dei dati pratica che poteva alterare importi gia'
// registrati senza passare da una rettifica esplicita. domain/practice/
// rules.py:effective_total_cents dimostra che in V2 l'unico input
// economico reale e' line_items (oltre all'override manuale) - questo
// test blinda la garanzia che il mapper usato da "Modifica pratica" e
// "Firma proprietario" non altera MAI line_items, a prescindere da quali
// altri campi vengono sovrascritti dal chiamante.
function buildPractice(overrides: Partial<Practice> = {}): Practice {
  return {
    id: 1,
    practice_number: "PP-000001",
    status: "ritirato",
    request_origin: "Collaboratore",
    originating_pickup_event_id: null,
    destination_branch_id: 1,
    client_id: 1,
    service_type: "Da decidere",
    collaborator_id: null,
    veterinarian_id: null,
    origin_veterinarian_id: null,
    pickup_type: "domicilio",
    pickup_location_id: null,
    pickup_zone_id: null,
    pickup_address: null,
    pickup_contact_name: null,
    provenance_code: null,
    microchip: null,
    notes: "Nota originale",
    ddt_number: 42,
    ddt_date: "2026-01-01",
    ddt_pdf_path: "DDT-000042-PP-000001.pdf",
    signature_data: "data:image/png;base64,AAA",
    data_complete: true,
    owner_snapshot: null,
    transport_method: null,
    vehicle_plate: null,
    temperature_mode: null,
    package_count: null,
    container_id: null,
    lot_number: null,
    treatment_method: null,
    delivery_at_clinic: false,
    delivery_at_home: false,
    signatory_identity_document_number: null,
    signatory_identity_document_date: null,
    signatory_signing_place: null,
    ddt_share_token: null,
    original_practice_number: null,
    computed_total_override_cents: null,
    computed_total_override_reason: null,
    computed_total_override_at: null,
    to_invoice: false,
    send_catalog: false,
    catalog_sent: false,
    send_estremi: false,
    estremi_sent: false,
    voucher_requested: false,
    use_voucher: false,
    whatsapp_thanks_sent_at: null,
    no_whatsapp_message: false,
    cremation_registered: false,
    cremation_queued: false,
    collaborator_billing_status: "non_dovuto",
    collaborator_billing_invoiced_at: null,
    collaborator_name_fallback: null,
    owner_notified_status: "non_inviato",
    owner_notified_at: null,
    used_voucher_id: null,
    deleted_at: null,
    created_at: "2026-01-01T10:00:00Z",
    updated_at: "2026-01-01T10:00:00Z",
    animals: [{ id: 1, sort_order: 0, name: "Fido", species: "Cane", breed: null, age_years: null, age_months: null, estimated_weight_grams: 12000, microchip: null, cremation_type: null }],
    line_items: [
      { id: 1, sort_order: 0, category: "cremazione", description: "Cremazione singola", subtype: null, amount_cents: 15000, channel: "W", urn_catalog_id: null },
      { id: 2, sort_order: 1, category: "urna", description: "Urna standard", subtype: null, amount_cents: 3000, channel: "W", urn_catalog_id: 7 },
    ],
    tags: [3, 5],
    line_items_total_cents: 18000,
    effective_total_cents: 18000,
    ...overrides,
  } as Practice;
}

describe("practiceToUpdateInput", () => {
  it("riporta line_items byte-per-byte identici (nessun campo economico alterato)", () => {
    const practice = buildPractice();
    const update = practiceToUpdateInput(practice);
    expect(update.line_items).toEqual([
      { category: "cremazione", description: "Cremazione singola", subtype: null, amount_cents: 15000, channel: "W", urn_catalog_id: null },
      { category: "urna", description: "Urna standard", subtype: null, amount_cents: 3000, channel: "W", urn_catalog_id: 7 },
    ]);
  });

  it("non perde e non duplica alcun line_item quando la pratica ne ha molti", () => {
    const manyItems = Array.from({ length: 12 }, (_, i) => ({
      id: i + 1,
      sort_order: i,
      category: "accessorio",
      description: `Voce ${i}`,
      subtype: null,
      amount_cents: (i + 1) * 100,
      channel: "W" as const,
      urn_catalog_id: null,
    }));
    const practice = buildPractice({ line_items: manyItems });
    const update = practiceToUpdateInput(practice);
    expect(update.line_items).toHaveLength(12);
    expect(update.line_items!.reduce((sum, li) => sum + li.amount_cents, 0)).toBe(
      manyItems.reduce((sum, li) => sum + li.amount_cents, 0),
    );
  });

  it("riporta i tag esattamente come tag_ids, senza aggiungerne o toglierne", () => {
    const practice = buildPractice({ tags: [3, 5] });
    expect(practiceToUpdateInput(practice).tag_ids).toEqual([3, 5]);
  });

  it("non tocca signature_data/ddt_* se il chiamante non li sovrascrive esplicitamente", () => {
    const practice = buildPractice();
    const update = practiceToUpdateInput(practice);
    expect(update.signature_data).toBe("data:image/png;base64,AAA");
    expect(update.ddt_number).toBe(42);
    expect(update.ddt_date).toBe("2026-01-01");
    expect(update.ddt_pdf_path).toBe("DDT-000042-PP-000001.pdf");
  });

  it("un override esplicito del chiamante (es. solo signature_data) lascia line_items/tag_ids invariati", () => {
    const practice = buildPractice();
    const update = { ...practiceToUpdateInput(practice), signature_data: "data:image/png;base64,NEW" };
    expect(update.signature_data).toBe("data:image/png;base64,NEW");
    expect(update.line_items).toEqual(practiceToUpdateInput(practice).line_items);
    expect(update.tag_ids).toEqual([3, 5]);
  });
});
