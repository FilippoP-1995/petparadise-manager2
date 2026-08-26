import enum
import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import pg_enum
from models.practice import PaymentChannel


class LedgerSection(str, enum.Enum):
    """doc06 '2. Pagamenti: un solo ledger' - dimensione indipendente dal
    channel (W/D/Collaboratori): un movimento e' sempre o un'Entrata o
    un'Uscita, indipendentemente da quale circuito lo classifica."""

    entrata = "Entrata"
    uscita = "Uscita"


class PaymentSource(str, enum.Enum):
    """doc06 Addendum N: distingue permanentemente un pagamento nato
    nativamente in V2 da uno migrato da V1 - utile durante tutto il
    periodo di verifica post-migrazione (doc07/11), non solo storico."""

    native = "native"
    v1_migration = "v1_migration"
    api = "api"
    automatic = "automatic"


class Payment(Base):
    """doc06 '2. Pagamenti: un solo ledger, circuito mai ambiguo' +
    Addendum N. Ledger append-only (FACT V1 preservato: trigger
    BEFORE UPDATE gia' presente su balance_movements) - qui l'append-only
    e' garantito non permettendo mai un service di scrivere un UPDATE
    (nessuna funzione lo fa), rinforzato da un trigger DB nella migrazione.

    'Quanto e' stato pagato' non e' mai un campo su questa tabella o su
    Practice: e' sempre SUM(amount_cents) calcolato da
    repositories/payment_repository.py, mai un valore memorizzato che
    potrebbe disallinearsi dal ledger (doc06 riga 117)."""

    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_cents <> 0", name="ck_payments_amount_cents_nonzero"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4)
    practice_id: Mapped[int | None] = mapped_column(ForeignKey("practices.id", ondelete="SET NULL"))
    practice_number_snapshot: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    movement_date: Mapped[date] = mapped_column(Date, nullable=False)
    channel: Mapped[PaymentChannel] = mapped_column(pg_enum(PaymentChannel, "payment_channel", create_type=False), nullable=False)
    ledger_section: Mapped[LedgerSection] = mapped_column(pg_enum(LedgerSection, "ledger_section"), nullable=False)
    # TEXT libero per scelta esplicita di doc06 (non un ENUM chiuso), vedi
    # riga 104 - valori reali osservati in V1: 'Acconto', 'Saldo',
    # 'Incasso completo', 'Storno', 'Entrata manuale', 'Uscita manuale'.
    movement_type: Mapped[str] = mapped_column(String(50), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BIGINT, nullable=False)
    payment_method: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    related_payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    collaborator_id: Mapped[int | None] = mapped_column(ForeignKey("collaborators.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    source: Mapped[PaymentSource] = mapped_column(
        pg_enum(PaymentSource, "payment_source"), nullable=False, default=PaymentSource.native
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)


class InvoicePaymentLink(Base):
    """doc06: collegamento esplicito fattura<->pagamento - un pagamento
    puo' esistere senza essere ancora collegato a una fattura (o non
    esserlo mai), un'azione separata e deliberata, mai automatica."""

    __tablename__ = "invoice_payment_links"

    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="CASCADE"), primary_key=True)


class PaymentDeletion(Base):
    """doc06 Addendum K 'Ledger reversibility - storni': snapshot completo
    PRIMA della cancellazione fisica di una riga payments - il meccanismo
    che rende gli storni/cancellazioni realmente ripristinabili.
    payment_id e' un riferimento informativo, non una FK (il pagamento
    originale puo' non esistere piu' dopo la cancellazione)."""

    __tablename__ = "payment_deletions"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    deletion_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
