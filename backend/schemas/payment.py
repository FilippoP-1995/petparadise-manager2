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
    idempotency_key: str | None = Field(default=None, max_length=150)
    """Facoltativa, fornita dal chiamante (stesso principio delle API
    Stripe-style "Idempotency-Key"): se un pagamento con questa chiave
    esiste gia', register_payment restituisce quello invece di crearne
    un secondo - un retry di rete o un doppio-click sulla stessa
    richiesta non deve mai produrre un secondo movimento reale. Se
    omessa, il comportamento e' invariato (chiave generata internamente,
    nessuna protezione di idempotenza per quella chiamata)."""


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


class PracticeInvoiceSummary(BaseModel):
    invoice_number: str
    total_amount_cents: int
    channel: PaymentChannel


class PracticePaymentsSummaryRead(BaseModel):
    """Fase 'parita' visiva V2' (Lista Pratiche, colonne Totale pagato/
    Fattura): riepilogo di sola lettura per un elenco di pratiche, in UNA
    query bulk per campo invece di N - NON e' la riconciliazione
    (PracticeReconciliationRead resta l'unica fonte per il dettaglio
    pagamenti di una pratica, invariata)."""

    practice_id: int
    paid_total_cents: int
    # V1 (Lista Pratiche, colonna 'Acconto' - channel_deposit()): solo i
    # movimenti di tipo Acconto (mai il Saldo), stesso sottoinsieme di
    # paid_total_cents con lo stesso filtro attivo/non-stornato.
    acconto_total_cents: int
    invoices: list[PracticeInvoiceSummary]
