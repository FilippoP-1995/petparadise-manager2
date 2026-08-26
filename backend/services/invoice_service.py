from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.errors import NotFoundError, ValidationDomainError
from domain.invoice.rules import classify_payment_status, ensure_invoice_channel_valid, ensure_positive_total
from models.invoice import Invoice
from repositories.audit_repository import AuditRepository
from repositories.invoice_repository import InvoiceRepository
from repositories.practice_repository import PracticeRepository
from schemas.invoice import InvoiceCreate, InvoiceReconciliationRead

ENTITY_TYPE = "invoice"


async def create_invoice(db: AsyncSession, data: InvoiceCreate, *, actor_user_id: int) -> Invoice:
    """FACT V1 (movement_invoices.practice_id NOT NULL, sempre valorizzato
    alla creazione in save_movement_invoice): una fattura nasce sempre
    legata a una pratica reale - la FK resta nullable solo per sopravvivere
    a una futura cancellazione della pratica (doc06 righe 89/312, confermato
    SET NULL, mai CASCADE)."""
    ensure_invoice_channel_valid(data.channel)
    ensure_positive_total(data.total_amount_cents)

    practice = await PracticeRepository(db).get_by_id(data.practice_id)
    if practice is None or practice.deleted_at is not None:
        raise NotFoundError(f"Pratica {data.practice_id} non trovata")

    repo = InvoiceRepository(db)
    audit = AuditRepository(db)

    invoice = Invoice(
        invoice_number=data.invoice_number,
        invoice_date=data.invoice_date,
        total_amount_cents=data.total_amount_cents,
        channel=data.channel,
        practice_id=practice.id,
        practice_number_snapshot=practice.practice_number,
        created_by=actor_user_id,
    )
    repo.add(invoice)
    # doc06 riga 69: UNIQUE (invoice_number) a livello DB - a differenza di
    # V1 (invoice_conflict(), solo un controllo applicativo check-then-write
    # senza vincolo DB, un TOCTOU reale gia' verificato) qui il vincolo e'
    # imposto dal database: due richieste concorrenti con lo stesso numero
    # possono entrambe superare una pre-verifica applicativa, ma solo una
    # puo' davvero commitare - l'altra deve ricevere un errore di dominio
    # leggibile, non un 500 non gestito. Il conflitto puo' emergere gia'
    # al flush (Postgres verifica un vincolo UNIQUE non deferrable subito),
    # non solo al commit - entrambi vanno protetti nello stesso blocco.
    try:
        await db.flush()
        audit.record(entity_type=ENTITY_TYPE, entity_id=invoice.id, action="created", user_id=actor_user_id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValidationDomainError(f"Numero fattura '{data.invoice_number}' gia' utilizzato.") from exc

    return await repo.get_by_id(invoice.id)


async def get_reconciliation(db: AsyncSession, invoice_id: int) -> InvoiceReconciliationRead:
    """doc06 Addendum O: fattura, pagato e residuo restano sempre distinti
    - mai una delle due cifre fatta collassare sull'altra."""
    repo = InvoiceRepository(db)
    invoice = await repo.get_by_id(invoice_id)
    if invoice is None:
        raise NotFoundError(f"Fattura {invoice_id} non trovata")

    paid_cents = await repo.paid_cents_for_invoice(invoice_id)
    return InvoiceReconciliationRead(
        invoice_id=invoice_id,
        total_amount_cents=invoice.total_amount_cents,
        paid_cents=paid_cents,
        residual_cents=invoice.total_amount_cents - paid_cents,
        status=classify_payment_status(invoice.total_amount_cents, paid_cents),
    )
