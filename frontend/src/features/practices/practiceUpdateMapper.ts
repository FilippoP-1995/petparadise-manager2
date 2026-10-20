import type { Practice, PracticeUpdateInput } from "./api";

// V2 (backend/services/practice_service.py:update_practice): PUT
// sostituisce SEMPRE l'intero payload (animals/line_items/tags inclusi),
// non esiste un PATCH parziale - a differenza di V1, che protegge
// esplicitamente i campi economici/documentali dall'editing generico
// (practice_autosave, app.py:17344: id/practice_number/status/.../
// ddt_number/ddt_pdf/signature_data sono "protected", mai scritti dal
// form di modifica dati).
//
// Questa funzione riproduce la stessa cautela: parte SEMPRE dai valori
// reali della pratica appena caricata (mai da uno stato ricostruito a
// mano) cosi' che line_items - il solo campo con un effetto economico
// reale, verificato in domain/practice/rules.py:effective_total_cents,
// che dipende SOLO da line_items e da computed_total_override_cents -
// non venga mai toccato da un form che non lo espone. I chiamanti
// sovrascrivono solo i campi che espongono davvero in UI.
export function practiceToUpdateInput(practice: Practice): PracticeUpdateInput {
  return {
    destination_branch_id: practice.destination_branch_id,
    request_origin: practice.request_origin,
    service_type: practice.service_type,
    collaborator_id: practice.collaborator_id,
    veterinarian_id: practice.veterinarian_id,
    origin_veterinarian_id: practice.origin_veterinarian_id,
    pickup_type: practice.pickup_type,
    pickup_location_id: practice.pickup_location_id,
    pickup_zone_id: practice.pickup_zone_id,
    pickup_address: practice.pickup_address,
    pickup_contact_name: practice.pickup_contact_name,
    provenance_code: practice.provenance_code,
    microchip: practice.microchip,
    notes: practice.notes,
    // Protetti anche qui, come in V1: mai riscritti da questo mapper se
    // non esplicitamente sovrascritti dal chiamante (Firma proprietario e'
    // l'unico flusso che tocca signature_data, vedi useSignPractice).
    ddt_number: practice.ddt_number,
    ddt_date: practice.ddt_date,
    ddt_pdf_path: practice.ddt_pdf_path,
    signature_data: practice.signature_data,
    transport_method: practice.transport_method,
    vehicle_plate: practice.vehicle_plate,
    temperature_mode: practice.temperature_mode,
    package_count: practice.package_count,
    container_id: practice.container_id,
    lot_number: practice.lot_number,
    treatment_method: practice.treatment_method,
    delivery_at_clinic: practice.delivery_at_clinic,
    delivery_at_home: practice.delivery_at_home,
    signatory_identity_document_number: practice.signatory_identity_document_number,
    signatory_identity_document_date: practice.signatory_identity_document_date,
    signatory_signing_place: practice.signatory_signing_place,
    to_invoice: practice.to_invoice,
    send_catalog: practice.send_catalog,
    send_estremi: practice.send_estremi,
    voucher_requested: practice.voucher_requested,
    use_voucher: practice.use_voucher,
    no_whatsapp_message: practice.no_whatsapp_message,
    animals: practice.animals.map((a) => ({
      name: a.name,
      species: a.species,
      breed: a.breed,
      age_years: a.age_years,
      age_months: a.age_months,
      estimated_weight_grams: a.estimated_weight_grams,
      microchip: a.microchip,
      cremation_type: a.cremation_type,
    })),
    // MAI ricostruiti a mano: esattamente gli stessi line_items gia'
    // presenti sulla pratica, stesso ordine, stessi importi/canale.
    line_items: practice.line_items.map((li) => ({
      category: li.category,
      description: li.description,
      subtype: li.subtype,
      amount_cents: li.amount_cents,
      channel: li.channel,
      urn_catalog_id: li.urn_catalog_id,
    })),
    tag_ids: practice.tags,
  };
}
