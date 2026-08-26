from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from models.practice import PaymentChannel


class InvoiceCreate(BaseModel):
    practice_id: int
    invoice_number: str
    invoice_date: date | None = None
    total_amount_cents: int
    channel: PaymentChannel


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    invoice_date: date | None
    total_amount_cents: int
    channel: PaymentChannel
    practice_id: int | None
    practice_number_snapshot: str
    created_at: datetime


class CorrectInvoiceTotalRequest(BaseModel):
    """doc06 Addendum R: correzione eccezionale - SOLO Admin (verificato a
    livello route via require_role), motivo obbligatorio, mai un PUT/PATCH
    generico. Il nuovo importo e' fornito esplicitamente, mai ricalcolato."""

    total_amount_cents: int
    reason: str = Field(min_length=1)


class InvoiceReconciliationRead(BaseModel):
    """doc06 Addendum O: le due cifre non collassano mai l'una sull'altra -
    fattura, pagato e residuo restano sempre distinti e visibili insieme."""

    invoice_id: int
    total_amount_cents: int
    paid_cents: int
    residual_cents: int
    status: str
