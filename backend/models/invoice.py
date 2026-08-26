from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import pg_enum
from models.practice import PaymentChannel


class Invoice(Base):
    """doc06 '1. Fatture: fonte unica' + Addendum O (riconciliazione).

    FACT V1 (verificato nel codice reale): ogni fattura e' sempre legata a
    una pratica al momento dell'emissione (sia il vecchio
    practices.invoice_number sia movement_invoices.practice_id sono sempre
    valorizzati alla creazione) - qui applicato come vincolo di dominio in
    create_invoice, non nello schema (practice_id resta nullable per
    sopravvivere alla cancellazione della pratica, doc06 righe 89/312:
    ON DELETE SET NULL confermato esplicitamente, mai CASCADE).

    total_amount_cents e' l'importo del DOCUMENTO FISCALE, deciso una volta
    all'emissione - non e' mai la somma dei pagamenti collegati (quella e'
    sempre calcolata a runtime da invoice_payment_links, vedi
    repositories/payment_repository.py). Nessun meccanismo di override
    dedicato qui (a differenza di practices.computed_total_override_* -
    doc06 Addendum D): l'unico vincolo dato e' 'immutabile salvo correzione
    esplicita tracciata in audit_log' - qui interpretato conservativamente
    come NESSUN endpoint di modifica in questo passaggio (non specificato
    altrove, non inventato)."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    total_amount_cents: Mapped[int] = mapped_column(BIGINT, nullable=False)
    # Solo W/D (mai 'Collaboratori'): la fatturazione al collaboratore e'
    # un flag di processo interno separato (doc06 Addendum F,
    # practices.collaborator_billing_status), mai un documento fiscale qui.
    channel: Mapped[PaymentChannel] = mapped_column(pg_enum(PaymentChannel, "payment_channel", create_type=False), nullable=False)
    practice_id: Mapped[int | None] = mapped_column(ForeignKey("practices.id", ondelete="SET NULL"))
    practice_number_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Solo created_at (mai updated_at): doc06 schema per invoices non ha
    # una colonna updated_at - un documento fiscale, una volta emesso, non
    # si "aggiorna", stesso principio gia' applicato ad audit_log/urn_movements.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
