import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from domain.errors import NotFoundError, ValidationDomainError
from domain.invoice.rules import classify_payment_status
from domain.payment.rules import ensure_nonzero_amount, ensure_not_already_reversed
from domain.practice.rules import effective_total_cents
from models.payment import InvoicePaymentLink, LedgerSection, Payment, PaymentDeletion, PaymentSource
from models.practice import PaymentChannel
from repositories.audit_repository import AuditRepository
from repositories.invoice_repository import InvoiceRepository
from repositories.payment_repository import PaymentDeletionRepository, PaymentRepository
from repositories.practice_repository import PracticeRepository
from repositories.reference_repositories import CollaboratorRepository
from schemas.payment import PaymentCreate, PracticeReconciliationRead

ENTITY_TYPE = "payment"
INVOICE_ENTITY_TYPE = "invoice"


def _new_idempotency_key() -> str:
    return f"payment:{uuid.uuid4()}"


async def register_payment(db: AsyncSession, data: PaymentCreate, *, actor_user_id: int) -> Payment:
    """FACT V1 (create_movement/balance_service.py): un pagamento e' sempre
    un INSERT puro sul ledger append-only - nessuna riga esistente viene
    letta e poi decisa in base al suo valore, quindi nessun lock necessario
    qui (a differenza di storno/cancellazione sotto, dove invece serve)."""
    ensure_nonzero_amount(data.amount_cents)

    practice_number_snapshot = ""
    if data.practice_id is not None:
        practice = await PracticeRepository(db).get_by_id(data.practice_id)
        if practice is None or practice.deleted_at is not None:
            raise NotFoundError(f"Pratica {data.practice_id} non trovata")
        practice_number_snapshot = practice.practice_number

    if data.collaborator_id is not None and await CollaboratorRepository(db).get_by_id(data.collaborator_id) is None:
        raise NotFoundError(f"Collaboratore {data.collaborator_id} non trovato")

    repo = PaymentRepository(db)
    audit = AuditRepository(db)

    payment = Payment(
        practice_id=data.practice_id,
        practice_number_snapshot=practice_number_snapshot,
        movement_date=data.movement_date,
        channel=data.channel,
        ledger_section=data.ledger_section,
        movement_type=data.movement_type,
        amount_cents=data.amount_cents,
        payment_method=data.payment_method,
        description=data.description,
        collaborator_id=data.collaborator_id,
        idempotency_key=_new_idempotency_key(),
        created_by=actor_user_id,
    )
    repo.add(payment)
    await db.flush()
    audit.record(entity_type=ENTITY_TYPE, entity_id=payment.id, action="created", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(payment.id)


async def link_payment_to_invoice(db: AsyncSession, invoice_id: int, payment_id: int, *, actor_user_id: int):
    invoice_repo = InvoiceRepository(db)
    payment_repo = PaymentRepository(db)
    audit = AuditRepository(db)

    invoice = await invoice_repo.get_by_id(invoice_id)
    if invoice is None:
        raise NotFoundError(f"Fattura {invoice_id} non trovata")
    payment = await payment_repo.get_by_id(payment_id)
    if payment is None:
        raise NotFoundError(f"Pagamento {payment_id} non trovato")

    db.add(InvoicePaymentLink(invoice_id=invoice_id, payment_id=payment_id))
    audit.record(
        entity_type=INVOICE_ENTITY_TYPE,
        entity_id=invoice_id,
        action="payment_linked",
        new_value=str(payment_id),
        user_id=actor_user_id,
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValidationDomainError("Questo pagamento e' gia' collegato a questa fattura.") from exc


async def reverse_payment(db: AsyncSession, payment_id: int, reason: str, *, actor_user_id: int) -> Payment:
    """doc06 Addendum K (storni): inserisce una riga di compensazione,
    l'originale non viene mai toccato (append-only). Lock sul pagamento
    originale PRIMA di verificare l'assenza di uno storno gia' esistente -
    e' esattamente il meccanismo che impedisce il doppio storno sotto
    richieste concorrenti (la seconda richiesta attende il lock, poi
    ritrova lo storno appena committato dalla prima e si arresta)."""
    payment_repo = PaymentRepository(db)
    audit = AuditRepository(db)

    original = await payment_repo.get_by_id_for_update(payment_id)
    if original is None:
        raise NotFoundError(f"Pagamento {payment_id} non trovato")

    existing_reversal = await payment_repo.get_active_reversal_for(payment_id)
    ensure_not_already_reversed(existing_reversal)

    reversal = Payment(
        practice_id=original.practice_id,
        practice_number_snapshot=original.practice_number_snapshot,
        movement_date=datetime.now(timezone.utc).date(),
        channel=original.channel,
        ledger_section=original.ledger_section,
        movement_type="Storno",
        amount_cents=-original.amount_cents,
        payment_method=original.payment_method,
        description=reason,
        related_payment_id=original.id,
        collaborator_id=original.collaborator_id,
        idempotency_key=_new_idempotency_key(),
        created_by=actor_user_id,
    )
    payment_repo.add(reversal)
    await db.flush()
    audit.record(
        entity_type=ENTITY_TYPE,
        entity_id=original.id,
        action="reversed",
        new_value=str(reversal.id),
        reason=reason,
        user_id=actor_user_id,
    )

    await db.commit()
    return await payment_repo.get_by_id(reversal.id)


def _snapshot_payment(payment: Payment) -> dict:
    return {
        "id": payment.id,
        "payment_uuid": str(payment.payment_uuid),
        "practice_id": payment.practice_id,
        "practice_number_snapshot": payment.practice_number_snapshot,
        "movement_date": payment.movement_date.isoformat(),
        "channel": payment.channel.value,
        "ledger_section": payment.ledger_section.value,
        "movement_type": payment.movement_type,
        "amount_cents": payment.amount_cents,
        "payment_method": payment.payment_method,
        "description": payment.description,
        "related_payment_id": payment.related_payment_id,
        "idempotency_key": payment.idempotency_key,
        "collaborator_id": payment.collaborator_id,
        "created_by": payment.created_by,
        "created_at": payment.created_at.isoformat(),
        "source": payment.source.value,
        "metadata_json": payment.metadata_json,
    }


async def delete_payment(db: AsyncSession, payment_id: int, deletion_kind: str, reason: str, *, actor_user_id: int) -> PaymentDeletion:
    """doc06 Addendum K: cancellazione fisica ECCEZIONALE (distinta dallo
    storno, che e' il percorso normale) - snapshot completo PRIMA della
    DELETE, cosi' da restare sempre ripristinabile. Se un altro pagamento
    punta a questo come storno (related_payment_id, ON DELETE RESTRICT),
    il database stesso rifiuta la cancellazione - non e' un caso da
    prevenire a mano, e' gia' garantito dal vincolo referenziale."""
    payment_repo = PaymentRepository(db)
    deletion_repo = PaymentDeletionRepository(db)
    audit = AuditRepository(db)

    payment = await payment_repo.get_by_id_for_update(payment_id)
    if payment is None:
        raise NotFoundError(f"Pagamento {payment_id} non trovato")

    deletion = PaymentDeletion(
        payment_id=payment.id,
        snapshot_json=_snapshot_payment(payment),
        deletion_kind=deletion_kind,
        deleted_by=actor_user_id,
    )
    deletion_repo.add(deletion)
    audit.record(entity_type=ENTITY_TYPE, entity_id=payment.id, action="deleted", reason=reason, user_id=actor_user_id)
    await payment_repo.delete(payment)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValidationDomainError(
            "Impossibile eliminare: questo pagamento ha uno storno collegato che lo referenzia."
        ) from exc

    await db.refresh(deletion)
    return deletion


async def restore_payment_deletion(db: AsyncSession, deletion_id: int, *, actor_user_id: int) -> Payment:
    deletion_repo = PaymentDeletionRepository(db)
    payment_repo = PaymentRepository(db)
    audit = AuditRepository(db)

    deletion = await deletion_repo.get_by_id(deletion_id)
    if deletion is None:
        raise NotFoundError(f"Cancellazione {deletion_id} non trovata")
    if deletion.restored_at is not None:
        raise ValidationDomainError("Questa cancellazione e' gia' stata ripristinata.")

    snap = deletion.snapshot_json
    restored = Payment(
        id=snap["id"],
        payment_uuid=uuid.UUID(snap["payment_uuid"]),
        practice_id=snap["practice_id"],
        practice_number_snapshot=snap["practice_number_snapshot"],
        movement_date=datetime.fromisoformat(snap["movement_date"]).date(),
        channel=PaymentChannel(snap["channel"]),
        ledger_section=LedgerSection(snap["ledger_section"]),
        movement_type=snap["movement_type"],
        amount_cents=snap["amount_cents"],
        payment_method=snap["payment_method"],
        description=snap["description"],
        related_payment_id=snap["related_payment_id"],
        idempotency_key=snap["idempotency_key"],
        collaborator_id=snap["collaborator_id"],
        created_by=snap["created_by"],
        source=PaymentSource(snap["source"]),
        metadata_json=snap["metadata_json"],
    )
    payment_repo.add(restored)

    deletion.restored_at = datetime.now(timezone.utc)
    deletion.restored_by = actor_user_id
    audit.record(entity_type=ENTITY_TYPE, entity_id=snap["id"], action="restored", user_id=actor_user_id)

    await db.commit()
    return await payment_repo.get_by_id(snap["id"])


async def get_practice_reconciliation(db: AsyncSession, practice_id: int) -> PracticeReconciliationRead:
    practice = await PracticeRepository(db).get_by_id(practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {practice_id} non trovata")

    payment_repo = PaymentRepository(db)
    total = effective_total_cents(practice)
    paid_w = await payment_repo.sum_paid_for_practice_channel(practice_id, PaymentChannel.W)
    paid_d = await payment_repo.sum_paid_for_practice_channel(practice_id, PaymentChannel.D)
    paid_collab = await payment_repo.sum_paid_for_practice_channel(practice_id, PaymentChannel.collaboratori)
    paid_total = paid_w + paid_d + paid_collab

    return PracticeReconciliationRead(
        practice_id=practice_id,
        effective_total_cents=total,
        paid_w_cents=paid_w,
        paid_d_cents=paid_d,
        paid_collaboratori_cents=paid_collab,
        paid_total_cents=paid_total,
        residual_cents=total - paid_total,
        status=classify_payment_status(total, paid_total),
    )
