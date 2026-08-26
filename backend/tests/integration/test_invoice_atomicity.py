"""Test di atomicita' specifici del dominio Fatture: la scrittura della
fattura e il relativo audit_log devono avvenire nella stessa transazione
(doc09 'Regola vincolante') - stessa tecnica di iniezione di fallimento
gia' usata negli altri domini di questa sessione (vincolo NOT NULL reale
su entity_type)."""

import pytest
from sqlalchemy.exc import IntegrityError

from models.invoice import Invoice
from models.practice import PaymentChannel
from repositories.audit_repository import AuditRepository
from repositories.invoice_repository import InvoiceRepository
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


async def test_failed_audit_write_rolls_back_invoice_creation(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    repo = InvoiceRepository(db_session)

    invoice = Invoice(
        invoice_number="FT-ATOM-1",
        total_amount_cents=34000,
        channel=PaymentChannel.W,
        practice_id=practice.id,
        practice_number_snapshot=practice.practice_number,
        created_by=admin_user.id,
    )
    repo.add(invoice)
    await db_session.flush()
    invoice_id = invoice.id

    AuditRepository(db_session).record(entity_type=None, entity_id=invoice_id, action="created", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    assert await repo.get_by_id(invoice_id) is None, "nessuna fattura orfana deve sopravvivere se l'audit fallisce"
    assert await repo.get_by_invoice_number("FT-ATOM-1") is None, "il numero fattura deve tornare libero dopo il rollback"
