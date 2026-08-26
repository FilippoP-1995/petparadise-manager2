import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.practice import PaymentChannel
from schemas.invoice import CorrectInvoiceTotalRequest, InvoiceCreate
from schemas.payment import PaymentCreate
from services import invoice_service, payment_service, practice_service
from schemas.practice import LineItemInput, PracticeCreate


async def _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=34000):
    practice = await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            line_items=[LineItemInput(category="Cremazione", description="Cremazione singola", amount_cents=total_cents, channel=PaymentChannel.W)],
        ),
        actor_user_id=admin_user.id,
    )
    return practice


def _invoice_data(practice_id, **overrides):
    base = dict(practice_id=practice_id, invoice_number="FT-0001", total_amount_cents=34000, channel=PaymentChannel.W)
    base.update(overrides)
    return InvoiceCreate(**base)


async def test_create_invoice(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)
    assert invoice.invoice_number == "FT-0001"
    assert invoice.practice_number_snapshot == practice.practice_number
    assert invoice.total_amount_cents == 34000


async def test_create_invoice_rejects_collaboratori_channel(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    with pytest.raises(ValidationDomainError):
        await invoice_service.create_invoice(
            db_session, _invoice_data(practice.id, channel=PaymentChannel.collaboratori), actor_user_id=admin_user.id
        )


async def test_create_invoice_rejects_duplicate_number(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    practice2 = await _create_practice(db_session, admin_user, sample_client, sample_location)
    with pytest.raises(ValidationDomainError):
        await invoice_service.create_invoice(db_session, _invoice_data(practice2.id), actor_user_id=admin_user.id)


async def test_create_invoice_unknown_practice_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await invoice_service.create_invoice(db_session, _invoice_data(999999), actor_user_id=admin_user.id)


async def test_reconciliation_non_pagata_with_no_payments(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "non_pagata"
    assert recon.paid_cents == 0
    assert recon.residual_cents == 34000


async def test_reconciliation_parziale_pagata_sovrapagata(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    async def _pay_and_link(amount_cents):
        payment = await payment_service.register_payment(
            db_session,
            PaymentCreate(
                practice_id=practice.id, movement_date="2026-01-01", channel=PaymentChannel.W,
                ledger_section="Entrata", movement_type="Acconto", amount_cents=amount_cents,
            ),
            actor_user_id=admin_user.id,
        )
        await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)
        return payment

    await _pay_and_link(12000)
    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "parziale"
    assert recon.paid_cents == 12000
    assert recon.residual_cents == 22000

    await _pay_and_link(22000)
    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "pagata"
    assert recon.residual_cents == 0

    await _pay_and_link(5000)
    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "sovrapagata", "il sovrapagamento e' uno stato visibile, mai auto-corretto"
    assert recon.paid_cents == 39000
    assert recon.residual_cents == -5000


async def test_reversed_payment_excluded_from_reconciliation(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    payment = await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=practice.id, movement_date="2026-01-01", channel=PaymentChannel.W,
            ledger_section="Entrata", movement_type="Saldo", amount_cents=34000,
        ),
        actor_user_id=admin_user.id,
    )
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)

    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "pagata"

    await payment_service.reverse_payment(db_session, payment.id, "errore importo", actor_user_id=admin_user.id)

    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "non_pagata", "il pagamento stornato non deve piu' contare come incassato"
    assert recon.paid_cents == 0


# --- doc06 Addendum R: correzione fattura emessa (correct_invoice_total) ---


async def test_correct_invoice_total_succeeds(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    corrected = await invoice_service.correct_invoice_total(
        db_session, invoice.id, CorrectInvoiceTotalRequest(total_amount_cents=30000, reason="errore di battitura"), actor_user_id=admin_user.id
    )
    assert corrected.total_amount_cents == 30000


async def test_correct_invoice_total_rejects_non_positive_amount(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await invoice_service.correct_invoice_total(
            db_session, invoice.id, CorrectInvoiceTotalRequest(total_amount_cents=0, reason="motivo"), actor_user_id=admin_user.id
        )


async def test_correct_invoice_total_unknown_invoice_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await invoice_service.correct_invoice_total(
            db_session, 999999, CorrectInvoiceTotalRequest(total_amount_cents=100, reason="motivo"), actor_user_id=admin_user.id
        )


async def test_correct_invoice_total_records_audit_with_old_new_reason_actor(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    await invoice_service.correct_invoice_total(
        db_session, invoice.id, CorrectInvoiceTotalRequest(total_amount_cents=30000, reason="errore di battitura"), actor_user_id=admin_user.id
    )

    from sqlalchemy import select

    from models.audit_log import AuditLog

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.entity_type == "invoice", AuditLog.entity_id == invoice.id, AuditLog.action == "total_corrected")
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.old_value == "34000"
    assert row.new_value == "30000"
    assert row.reason == "errore di battitura"
    assert row.user_id == admin_user.id
    assert row.created_at is not None


async def test_correct_invoice_total_does_not_touch_existing_payments(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)
    payment = await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=practice.id, movement_date="2026-01-01", channel=PaymentChannel.W,
            ledger_section="Entrata", movement_type="Acconto", amount_cents=12000,
        ),
        actor_user_id=admin_user.id,
    )
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)

    await invoice_service.correct_invoice_total(
        db_session, invoice.id, CorrectInvoiceTotalRequest(total_amount_cents=30000, reason="correzione"), actor_user_id=admin_user.id
    )

    from repositories.payment_repository import PaymentRepository

    reloaded_payment = await PaymentRepository(db_session).get_by_id(payment.id)
    assert reloaded_payment.amount_cents == 12000, "il pagamento non deve mai essere modificato dalla correzione della fattura"


async def test_reconciliation_recalculated_after_correction(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)
    payment = await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=practice.id, movement_date="2026-01-01", channel=PaymentChannel.W,
            ledger_section="Entrata", movement_type="Acconto", amount_cents=20000,
        ),
        actor_user_id=admin_user.id,
    )
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)

    recon_before = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon_before.status == "parziale"  # 20000 pagati su 34000

    # Corregge il totale a un valore inferiore al gia' pagato - deve
    # risultare sovrapagata, mai corretta automaticamente.
    await invoice_service.correct_invoice_total(
        db_session, invoice.id, CorrectInvoiceTotalRequest(total_amount_cents=15000, reason="sconto applicato dopo l'emissione"), actor_user_id=admin_user.id
    )
    recon_after = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon_after.total_amount_cents == 15000
    assert recon_after.paid_cents == 20000, "i pagamenti collegati restano invariati"
    assert recon_after.residual_cents == -5000
    assert recon_after.status == "sovrapagata"


async def test_correct_invoice_total_leaves_number_practice_and_snapshot_unchanged(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)

    corrected = await invoice_service.correct_invoice_total(
        db_session, invoice.id, CorrectInvoiceTotalRequest(total_amount_cents=1, reason="test invarianza campi"), actor_user_id=admin_user.id
    )
    assert corrected.invoice_number == invoice.invoice_number
    assert corrected.practice_id == invoice.practice_id
    assert corrected.practice_number_snapshot == invoice.practice_number_snapshot
    assert corrected.channel == invoice.channel
    assert corrected.created_at == invoice.created_at


async def test_correct_invoice_total_never_deletes_invoice(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(db_session, _invoice_data(practice.id), actor_user_id=admin_user.id)
    invoice_id = invoice.id

    await invoice_service.correct_invoice_total(
        db_session, invoice_id, CorrectInvoiceTotalRequest(total_amount_cents=1, reason="motivo"), actor_user_id=admin_user.id
    )

    from repositories.invoice_repository import InvoiceRepository

    assert await InvoiceRepository(db_session).get_by_id(invoice_id) is not None
