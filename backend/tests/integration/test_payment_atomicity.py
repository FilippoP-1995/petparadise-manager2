"""Test di atomicita' specifici del dominio Pagamenti - ognuno replica
manualmente i passi del rispettivo service, inietta un fallimento reale
nell'ultimo passo (vincolo NOT NULL su entity_type) e verifica che NESSUNA
scrittura finanziaria parziale e NESSUN audit orfano sopravvivano al
rollback. Non e' sufficiente una verifica statica del codice - qui il
fallimento e' realmente forzato e osservato."""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from models.payment import Payment, PaymentDeletion
from repositories.audit_repository import AuditRepository
from repositories.payment_repository import PaymentDeletionRepository, PaymentRepository
from schemas.practice import LineItemInput, PracticeCreate
from services import practice_service


async def _create_practice(db_session, admin_user, sample_client, sample_location):
    return await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            line_items=[LineItemInput(category="Cremazione", description="Cremazione singola", amount_cents=34000)],
        ),
        actor_user_id=admin_user.id,
    )


async def test_failed_audit_write_rolls_back_payment_registration(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    repo = PaymentRepository(db_session)

    payment = Payment(
        practice_id=practice.id,
        practice_number_snapshot=practice.practice_number,
        movement_date=date(2026, 1, 1),
        channel="W",
        ledger_section="Entrata",
        movement_type="Acconto",
        amount_cents=12000,
        idempotency_key="atomicity-register-1",
        created_by=admin_user.id,
    )
    repo.add(payment)
    await db_session.flush()
    payment_id = payment.id

    AuditRepository(db_session).record(entity_type=None, entity_id=payment_id, action="created", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    assert await repo.get_by_id(payment_id) is None, "nessun pagamento orfano deve sopravvivere se l'audit fallisce"


async def test_failed_audit_write_rolls_back_reversal(db_session, admin_user, sample_client, sample_location):
    """Replica dei passi di reverse_payment: se l'ultimo passo fallisce,
    ne' lo storno ne' l'originale devono risultare alterati - l'originale
    in particolare non deve MAI essere toccato (append-only)."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    repo = PaymentRepository(db_session)

    original = Payment(
        practice_id=practice.id,
        practice_number_snapshot=practice.practice_number,
        movement_date=date(2026, 1, 1),
        channel="W",
        ledger_section="Entrata",
        movement_type="Acconto",
        amount_cents=12000,
        idempotency_key="atomicity-reversal-original",
        created_by=admin_user.id,
    )
    repo.add(original)
    await db_session.flush()
    await db_session.commit()
    original_id = original.id

    reloaded = await repo.get_by_id_for_update(original_id)
    reversal = Payment(
        practice_id=reloaded.practice_id,
        practice_number_snapshot=reloaded.practice_number_snapshot,
        movement_date=date(2026, 1, 2),
        channel=reloaded.channel,
        ledger_section=reloaded.ledger_section,
        movement_type="Storno",
        amount_cents=-reloaded.amount_cents,
        related_payment_id=reloaded.id,
        idempotency_key="atomicity-reversal-storno",
        created_by=admin_user.id,
    )
    repo.add(reversal)
    await db_session.flush()

    AuditRepository(db_session).record(entity_type=None, entity_id=original_id, action="reversed", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final_original = await repo.get_by_id(original_id)
    assert final_original.amount_cents == 12000, "l'originale non deve mai risultare alterato"

    reversal_still_exists = await repo.get_active_reversal_for(original_id)
    assert reversal_still_exists is None, "nessuno storno orfano deve sopravvivere al rollback"


async def test_failed_audit_write_rolls_back_payment_deletion(db_session, admin_user, sample_client, sample_location):
    """Replica dei passi di delete_payment: se l'ultimo passo fallisce,
    il pagamento NON deve risultare cancellato e nessuno snapshot orfano
    deve sopravvivere - la cancellazione fisica e' irreversibile, quindi
    il punto piu' critico da verificare in atomicita'."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment_repo = PaymentRepository(db_session)
    deletion_repo = PaymentDeletionRepository(db_session)

    payment = Payment(
        practice_id=practice.id,
        practice_number_snapshot=practice.practice_number,
        movement_date=date(2026, 1, 1),
        channel="W",
        ledger_section="Entrata",
        movement_type="Acconto",
        amount_cents=12000,
        idempotency_key="atomicity-deletion-1",
        created_by=admin_user.id,
    )
    payment_repo.add(payment)
    await db_session.flush()
    await db_session.commit()
    payment_id = payment.id

    reloaded = await payment_repo.get_by_id_for_update(payment_id)
    deletion = PaymentDeletion(
        payment_id=reloaded.id,
        snapshot_json={"id": reloaded.id, "amount_cents": reloaded.amount_cents},
        deletion_kind="errore_inserimento",
        deleted_by=admin_user.id,
    )
    deletion_repo.add(deletion)
    await db_session.flush()

    # Replica esatta dell'ordine di delete_payment: la DELETE fisica del
    # pagamento avviene DENTRO la stessa transazione, prima del commit -
    # il fallimento forzato qui verifica che anche una DELETE gia' eseguita
    # (ma non ancora committata) venga davvero annullata dal rollback, non
    # solo le INSERT precedenti.
    await payment_repo.delete(reloaded)
    AuditRepository(db_session).record(entity_type=None, entity_id=payment_id, action="deleted", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    assert await payment_repo.get_by_id(payment_id) is not None, "il pagamento NON deve risultare cancellato se la transazione fallisce"

    from sqlalchemy import select

    orphan_deletions = (
        await db_session.execute(select(PaymentDeletion).where(PaymentDeletion.payment_id == payment_id))
    ).scalars().all()
    assert orphan_deletions == [], "nessuno snapshot di cancellazione orfano deve sopravvivere al rollback"
