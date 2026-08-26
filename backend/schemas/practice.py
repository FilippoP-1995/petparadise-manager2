from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field

from domain.practice.rules import effective_total_cents as _effective_total_cents
from models.practice import CollaboratorBillingStatus, OwnerNotifiedStatus, PaymentChannel, PickupType, PracticeStatus


class AnimalInput(BaseModel):
    name: str | None = None
    species: str | None = None
    breed: str | None = None
    age_years: int | None = None
    age_months: int | None = None
    estimated_weight_grams: int | None = None
    microchip: str | None = None
    cremation_type: str | None = None


class AnimalRead(AnimalInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sort_order: int


class LineItemInput(BaseModel):
    category: str
    description: str
    subtype: str | None = None
    amount_cents: int
    channel: PaymentChannel = PaymentChannel.W
    urn_catalog_id: int | None = None


class LineItemRead(LineItemInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sort_order: int


class _PracticeMutableFields(BaseModel):
    """Campi condivisi da creazione e modifica generale - NON include mai
    `status` (doc09 'lo stato iniziale non e' mai un parametro di
    creazione') ne' i campi che doc06 tratta come azioni dedicate separate
    (transizione stato, cestino, override totale, notifica proprietario,
    fatturazione collaboratore - vedi le rispettive route)."""

    destination_branch_id: int
    request_origin: str
    service_type: str
    collaborator_id: int | None = None
    veterinarian_id: int | None = None
    origin_veterinarian_id: int | None = None

    pickup_type: PickupType = PickupType.domicilio
    pickup_location_id: int | None = None
    pickup_zone_id: int | None = None
    pickup_address: str | None = None
    pickup_contact_name: str | None = None
    provenance_code: str | None = None

    microchip: str | None = None
    notes: str | None = None

    ddt_number: int | None = None
    ddt_date: date | None = None
    ddt_pdf_path: str | None = None
    signature_data: str | None = None

    transport_method: str | None = None
    vehicle_plate: str | None = None
    temperature_mode: str | None = None
    package_count: int | None = None
    container_id: str | None = None
    lot_number: str | None = None
    treatment_method: str | None = None
    delivery_at_clinic: bool = False
    delivery_at_home: bool = False
    signatory_identity_document_number: str | None = None
    signatory_identity_document_date: date | None = None
    signatory_signing_place: str | None = None

    to_invoice: bool = False
    send_catalog: bool = False
    send_estremi: bool = False
    voucher_requested: bool = False
    use_voucher: bool = False
    no_whatsapp_message: bool = False

    animals: list[AnimalInput] = Field(default_factory=list)
    line_items: list[LineItemInput] = Field(default_factory=list)
    tag_ids: list[int] = Field(default_factory=list)


class PracticeCreate(_PracticeMutableFields):
    client_id: int


class PracticeUpdate(_PracticeMutableFields):
    """client_id NON e' modificabile dopo la creazione (doc06 Addendum A:
    owner_snapshot e' legato al cliente al momento della creazione - farlo
    scivolare su un cliente diverso dopo renderebbe ambiguo cosa rappresenta
    lo snapshot storico)."""


class TransitionRequest(BaseModel):
    target_status: PracticeStatus


class CorrectionRequest(BaseModel):
    target_status: PracticeStatus
    reason: str = Field(min_length=1)


class OverrideTotalRequest(BaseModel):
    amount_cents: int
    reason: str = Field(min_length=1)


class TrashRequest(BaseModel):
    reason: str | None = None


class PracticeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    practice_number: str
    status: PracticeStatus
    request_origin: str
    originating_pickup_event_id: int | None
    destination_branch_id: int
    client_id: int
    service_type: str
    collaborator_id: int | None
    veterinarian_id: int | None
    origin_veterinarian_id: int | None

    pickup_type: PickupType
    pickup_location_id: int | None
    pickup_zone_id: int | None
    pickup_address: str | None
    pickup_contact_name: str | None
    provenance_code: str | None

    microchip: str | None
    notes: str | None

    ddt_number: int | None
    ddt_date: date | None
    ddt_pdf_path: str | None
    signature_data: str | None
    data_complete: bool

    owner_snapshot: dict | None

    transport_method: str | None
    vehicle_plate: str | None
    temperature_mode: str | None
    package_count: int | None
    container_id: str | None
    lot_number: str | None
    treatment_method: str | None
    delivery_at_clinic: bool
    delivery_at_home: bool
    signatory_identity_document_number: str | None
    signatory_identity_document_date: date | None
    signatory_signing_place: str | None
    ddt_share_token: str | None
    original_practice_number: str | None

    computed_total_override_cents: int | None
    computed_total_override_reason: str | None
    computed_total_override_at: datetime | None
    to_invoice: bool

    send_catalog: bool
    catalog_sent: bool
    send_estremi: bool
    estremi_sent: bool
    voucher_requested: bool
    use_voucher: bool
    whatsapp_thanks_sent_at: datetime | None
    no_whatsapp_message: bool
    cremation_registered: bool
    cremation_queued: bool

    collaborator_billing_status: CollaboratorBillingStatus
    collaborator_billing_invoiced_at: datetime | None
    collaborator_name_fallback: str | None

    owner_notified_status: OwnerNotifiedStatus
    owner_notified_at: datetime | None

    used_voucher_id: int | None

    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    animals: list[AnimalRead]
    line_items: list[LineItemRead]
    tags: list[int] = Field(default_factory=list)

    line_items_total_cents: int = 0
    # dominio Fatture/Pagamenti: espone domain.practice.rules.effective_total_cents
    # (override se presente, altrimenti somma preventivo) - mai ricalcolato
    # in parallelo con una propria formula, stessa funzione gia' riusata da
    # domain/delivery/rules.py per la riconciliazione Riconsegna.
    effective_total_cents: int = 0

    @classmethod
    def from_practice(cls, practice) -> "PracticeRead":
        data = cls.model_validate(practice).model_dump()
        data["tags"] = [t.id for t in practice.tags]
        data["line_items_total_cents"] = sum(li.amount_cents for li in practice.line_items)
        data["effective_total_cents"] = _effective_total_cents(practice)
        return cls(**data)
