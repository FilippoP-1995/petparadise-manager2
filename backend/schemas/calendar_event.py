from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models.calendar_event import DeliveryType, PickupStatus
from models.practice import PickupType
from schemas.practice import AnimalInput, AnimalRead


class PickupCreate(BaseModel):
    """Mai un campo pickup_status qui (doc09 'lo stato iniziale non e' mai
    un parametro di creazione', principio esteso a ogni entita' con FSM,
    non solo Pratica) - hardcoded server-side a 'da_confermare' (FACT: V1
    stesso default, calendar_service.py normalize_event)."""

    start_at: datetime
    end_at: datetime
    client_id: int | None = None
    veterinarian_id: int | None = None
    collaborator_id: int | None = None
    pickup_type: PickupType
    pickup_location_id: int | None = None
    pickup_zone_id: int | None = None
    pickup_address: str | None = None
    pickup_contact_name: str | None = None
    notes: str | None = None
    animals: list[AnimalInput] = Field(default_factory=list)


class PickupUpdate(PickupCreate):
    """Stessi campi di creazione. Un ritiro 'annullato' e' terminale e non
    e' modificabile (doc.14 §2 + sezione 6 della richiesta corrente) -
    verificato nel service, non nello schema."""


class PickupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pickup_status: PickupStatus
    start_at: datetime
    end_at: datetime
    client_id: int | None
    veterinarian_id: int | None
    collaborator_id: int | None
    pickup_type: PickupType
    pickup_location_id: int | None
    pickup_zone_id: int | None
    pickup_address: str | None
    pickup_contact_name: str | None
    notes: str | None
    linked_practice_id: int | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    animals: list[AnimalRead]


class TransitionPickupRequest(BaseModel):
    target_status: PickupStatus


class CancelPickupRequest(BaseModel):
    reason: str | None = None


class CancelPickupAndTrashPracticeRequest(BaseModel):
    """doc.15 sezione 6B: azione distinta, conferma esplicita separata -
    motivo sempre obbligatorio qui (a differenza dell'annullamento
    normale), proprio perche' comporta la cestinazione anche della pratica."""

    reason: str = Field(min_length=1)


class CreatePracticeFromPickupRequest(BaseModel):
    """Percorso A. destination_branch_id/service_type restano una scelta
    esplicita dell'operatore al momento della creazione (doc06 Addendum C:
    'niente viene mai dedotto') - non copiati automaticamente da nessun
    campo del Ritiro, che non li possiede."""

    destination_branch_id: int
    service_type: str = "Da decidere"


class DeliveryCreate(BaseModel):
    start_at: datetime
    end_at: datetime
    client_id: int | None = None
    delivery_type: DeliveryType
    delivery_veterinarian_id: int | None = None
    delivery_location_id: int | None = None
    delivery_zone_id: int | None = None
    delivery_address: str | None = None
    notes: str | None = None
    linked_practice_id: int | None = None
    preliminary_payment_status: str | None = None
    preliminary_payment_amount: int | None = None


class DeliveryUpdate(BaseModel):
    """linked_practice_id NON e' qui: il collegamento passa solo dalla
    azione dedicata /link (doc06 Addendum P, riconciliazione esplicita).
    preliminary_payment_* restano modificabili SOLO se la riconsegna non e'
    ancora collegata - verificato nel service (congelamento)."""

    start_at: datetime
    end_at: datetime
    client_id: int | None = None
    delivery_type: DeliveryType
    delivery_veterinarian_id: int | None = None
    delivery_location_id: int | None = None
    delivery_zone_id: int | None = None
    delivery_address: str | None = None
    notes: str | None = None
    preliminary_payment_status: str | None = None
    preliminary_payment_amount: int | None = None


class DeliveryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    start_at: datetime
    end_at: datetime
    client_id: int | None
    delivery_type: DeliveryType
    delivery_veterinarian_id: int | None
    delivery_location_id: int | None
    delivery_zone_id: int | None
    delivery_address: str | None
    notes: str | None
    linked_practice_id: int | None
    preliminary_payment_status: str | None
    preliminary_payment_amount: int | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LinkDeliveryToPracticeRequest(BaseModel):
    practice_id: int
    confirm_despite_mismatch: bool = False
