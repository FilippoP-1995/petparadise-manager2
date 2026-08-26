from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models.payment import LedgerSection, PaymentSource
from models.practice import PaymentChannel


class PaymentCreate(BaseModel):
    practice_id: int | None = None
    movement_date: date
    channel: PaymentChannel
    ledger_section: LedgerSection
    movement_type: str
    amount_cents: int
    payment_method: str | None = None
    description: str | None = None
    collaborator_id: int | None = None


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_uuid: UUID
    practice_id: int | None
    practice_number_snapshot: str
    movement_date: date
    channel: PaymentChannel
    ledger_section: LedgerSection
    movement_type: str
    amount_cents: int
    payment_method: str | None
    description: str | None
    related_payment_id: int | None
    collaborator_id: int | None
    source: PaymentSource
    created_at: datetime


class LinkPaymentToInvoiceRequest(BaseModel):
    payment_id: int


class ReversePaymentRequest(BaseModel):
    reason: str = Field(min_length=1)


class DeletePaymentRequest(BaseModel):
    deletion_kind: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class PaymentDeletionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_id: int
    deletion_kind: str
    deleted_by: int | None
    deleted_at: datetime
    restored_at: datetime | None
    restored_by: int | None


class PracticeReconciliationRead(BaseModel):
    """Riconciliazione a livello pratica (non fattura): totale effettivo
    (override se presente, altrimenti somma preventivo - domain.practice.
    rules.effective_total_cents, mai ricalcolato in parallelo) contro
    quanto risulta pagato sul ledger, per canale."""

    practice_id: int
    effective_total_cents: int
    paid_w_cents: int
    paid_d_cents: int
    paid_collaboratori_cents: int
    paid_total_cents: int
    residual_cents: int
    status: str
