import pytest

from domain.errors import NotFoundError, ValidationDomainError
from models.practice import PaymentChannel
from schemas.invoice import InvoiceCreate
from schemas.payment import DeletePaymentRequest, PaymentCreate
from schemas.practice import LineItemInput, PracticeCreate
from services import invoice_service, payment_service, practice_service


async def _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=34000):
    return await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            line_items=[LineItemInput(category="Cremazione", description="Cremazione singola", amount_cents=total_cents)],
        ),
        actor_user_id=admin_user.id,
    )


def _payment_data(practice_id=None, **overrides):
    base = dict(
        practice_id=practice_id, movement_date="2026-01-01", channel=PaymentChannel.W,
        ledger_section="Entrata", movement_type="Acconto", amount_cents=12000,
    )
    base.update(overrides)
    return PaymentCreate(**base)


async def test_register_payment(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)
    assert payment.amount_cents == 12000
    assert payment.practice_number_snapshot == practice.practice_number
    assert payment.idempotency_key


async def test_register_payment_without_practice_allowed(db_session, admin_user):
    """FACT V1 (balance_movements.practice_id nullable, source
    'manual_income'/'manual_expense'): un movimento di contabilita'
    generale non deve necessariamente essere legato a una pratica."""
    payment = await payment_service.register_payment(
        db_session, _payment_data(None, movement_type="Entrata manuale"), actor_user_id=admin_user.id
    )
    assert payment.practice_id is None
    assert payment.practice_number_snapshot == ""


async def test_register_payment_rejects_zero_amount(db_session, admin_user):
    with pytest.raises(ValidationDomainError):
        await payment_service.register_payment(db_session, _payment_data(None, amount_cents=0), actor_user_id=admin_user.id)


async def test_register_payment_unknown_practice_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await payment_service.register_payment(db_session, _payment_data(999999), actor_user_id=admin_user.id)


async def test_register_payment_unknown_collaborator_raises_not_found(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    with pytest.raises(NotFoundError):
        await payment_service.register_payment(
            db_session, _payment_data(practice.id, collaborator_id=999999), actor_user_id=admin_user.id
        )


async def test_link_payment_to_invoice(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-LINK-1", total_amount_cents=34000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)

    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)
    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.paid_cents == 12000


async def test_link_payment_to_invoice_rejects_duplicate(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-LINK-2", total_amount_cents=34000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)


async def test_reverse_payment_creates_compensating_row(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)

    reversal = await payment_service.reverse_payment(db_session, payment.id, "errore operatore", actor_user_id=admin_user.id)
    assert reversal.movement_type == "Storno"
    assert reversal.amount_cents == -12000
    assert reversal.related_payment_id == payment.id

    # l'originale non viene mai toccato (append-only) - verificato leggendolo di nuovo
    from repositories.payment_repository import PaymentRepository

    original = await PaymentRepository(db_session).get_by_id(payment.id)
    assert original.amount_cents == 12000


async def test_reverse_payment_twice_rejected(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)
    await payment_service.reverse_payment(db_session, payment.id, "primo storno", actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await payment_service.reverse_payment(db_session, payment.id, "secondo tentativo", actor_user_id=admin_user.id)


async def test_reverse_unknown_payment_raises_not_found(db_session, admin_user):
    with pytest.raises(NotFoundError):
        await payment_service.reverse_payment(db_session, 999999, "motivo", actor_user_id=admin_user.id)


async def test_delete_payment_snapshots_and_removes_row(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)
    payment_id = payment.id

    deletion = await payment_service.delete_payment(
        db_session, payment_id, "errore_inserimento", "riga duplicata per errore", actor_user_id=admin_user.id
    )
    assert deletion.payment_id == payment_id
    assert deletion.snapshot_json["amount_cents"] == 12000

    from repositories.payment_repository import PaymentRepository

    assert await PaymentRepository(db_session).get_by_id(payment_id) is None, "la riga deve essere fisicamente rimossa"


async def test_delete_payment_blocked_when_already_reversed(db_session, admin_user, sample_client, sample_location):
    """doc06 Addendum K: related_payment_id ON DELETE RESTRICT - un
    pagamento gia' stornato non puo' essere eliminato fisicamente, il
    vincolo referenziale lo impedisce a livello database."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)
    await payment_service.reverse_payment(db_session, payment.id, "storno", actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await payment_service.delete_payment(
            db_session, payment.id, "errore_inserimento", "tentativo dopo storno", actor_user_id=admin_user.id
        )


async def test_restore_payment_deletion_roundtrip(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)
    payment_id = payment.id

    deletion = await payment_service.delete_payment(
        db_session, payment_id, "errore_inserimento", "da ripristinare", actor_user_id=admin_user.id
    )
    restored = await payment_service.restore_payment_deletion(db_session, deletion.id, actor_user_id=admin_user.id)
    assert restored.id == payment_id
    assert restored.amount_cents == 12000

    from repositories.payment_repository import PaymentDeletionRepository

    reloaded_deletion = await PaymentDeletionRepository(db_session).get_by_id(deletion.id)
    assert reloaded_deletion.restored_at is not None
    assert reloaded_deletion.restored_by == admin_user.id


async def test_restore_already_restored_deletion_rejected(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)
    payment = await payment_service.register_payment(db_session, _payment_data(practice.id), actor_user_id=admin_user.id)
    deletion = await payment_service.delete_payment(
        db_session, payment.id, "errore_inserimento", "motivo", actor_user_id=admin_user.id
    )
    await payment_service.restore_payment_deletion(db_session, deletion.id, actor_user_id=admin_user.id)

    with pytest.raises(ValidationDomainError):
        await payment_service.restore_payment_deletion(db_session, deletion.id, actor_user_id=admin_user.id)


async def test_practice_reconciliation_partial_full_overpaid(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=34000)

    recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
    assert recon.status == "non_pagata"
    assert recon.effective_total_cents == 34000

    await payment_service.register_payment(db_session, _payment_data(practice.id, amount_cents=12000), actor_user_id=admin_user.id)
    recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
    assert recon.status == "parziale"
    assert recon.paid_total_cents == 12000

    await payment_service.register_payment(
        db_session, _payment_data(practice.id, amount_cents=22000, movement_type="Saldo"), actor_user_id=admin_user.id
    )
    recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
    assert recon.status == "pagata"

    await payment_service.register_payment(
        db_session, _payment_data(practice.id, amount_cents=5000, movement_type="Saldo"), actor_user_id=admin_user.id
    )
    recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
    assert recon.status == "sovrapagata"
    assert recon.residual_cents == -5000


async def test_practice_reconciliation_splits_by_channel(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=20000)
    await payment_service.register_payment(db_session, _payment_data(practice.id, amount_cents=10000, channel=PaymentChannel.W), actor_user_id=admin_user.id)
    await payment_service.register_payment(db_session, _payment_data(practice.id, amount_cents=5000, channel=PaymentChannel.D), actor_user_id=admin_user.id)

    recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
    assert recon.paid_w_cents == 10000
    assert recon.paid_d_cents == 5000
    assert recon.paid_collaboratori_cents == 0
    assert recon.paid_total_cents == 15000


async def test_practice_reconciliation_uses_override_not_line_items(db_session, admin_user, sample_client, sample_location):
    """L'invariante esplicitamente richiesta: la riconciliazione deve
    usare SEMPRE domain.practice.rules.effective_total_cents, mai
    ricalcolare il totale in parallelo - se c'e' un override, quello e'
    il totale, non la somma preventivo."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=34000)

    from schemas.practice import OverrideTotalRequest

    await practice_service.set_total_override(
        db_session, practice.id, OverrideTotalRequest(amount_cents=20000, reason="sconto concordato"), actor_user_id=admin_user.id
    )

    recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
    assert recon.effective_total_cents == 20000, "deve riflettere l'override, non la somma preventivo (34000)"


async def test_override_survives_line_items_recalculation(db_session, admin_user, sample_client, sample_location):
    """Invariante esplicitamente richiesta: un ricalcolo automatico (qui
    simulato aggiornando i line_items della pratica) non deve mai
    sovrascrivere silenziosamente un override manuale gia' impostato."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=34000)

    from schemas.practice import OverrideTotalRequest, PracticeUpdate

    await practice_service.set_total_override(
        db_session, practice.id, OverrideTotalRequest(amount_cents=20000, reason="sconto concordato"), actor_user_id=admin_user.id
    )

    update_data = PracticeUpdate(
        destination_branch_id=practice.destination_branch_id,
        request_origin=practice.request_origin,
        service_type=practice.service_type,
        line_items=[LineItemInput(category="Cremazione", description="Nuovo importo ricalcolato", amount_cents=99000)],
    )
    updated = await practice_service.update_practice(db_session, practice.id, update_data, actor_user_id=admin_user.id)

    assert updated.computed_total_override_cents == 20000, "l'override non deve essere toccato da un aggiornamento dei line_items"
    from domain.practice.rules import effective_total_cents

    assert effective_total_cents(updated) == 20000


async def test_clear_override_returns_to_line_items_total(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=34000)

    from schemas.practice import OverrideTotalRequest

    await practice_service.set_total_override(
        db_session, practice.id, OverrideTotalRequest(amount_cents=20000, reason="sconto"), actor_user_id=admin_user.id
    )
    cleared = await practice_service.clear_total_override(db_session, practice.id, actor_user_id=admin_user.id)

    assert cleared.computed_total_override_cents is None
    from domain.practice.rules import effective_total_cents

    assert effective_total_cents(cleared) == 34000


async def test_collaborator_billing_stays_independent_of_invoices_and_payments(
    db_session, admin_user, sample_client, sample_location, sample_collaborator
):
    """doc06 Addendum F: collaborator_billing_status e' un flag di
    processo interno, mai un documento fiscale - registrare fatture/
    pagamenti su una pratica non deve toccarlo, e viceversa."""
    practice = await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            collaborator_id=sample_collaborator.id,
            line_items=[LineItemInput(category="Cremazione", description="Cremazione singola", amount_cents=34000)],
        ),
        actor_user_id=admin_user.id,
    )
    assert practice.collaborator_billing_status.value == "da_fatturare"

    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-COLLAB-1", total_amount_cents=34000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )
    full_payment = await payment_service.register_payment(
        db_session, _payment_data(practice.id, amount_cents=34000, movement_type="Incasso completo"), actor_user_id=admin_user.id
    )
    await payment_service.link_payment_to_invoice(db_session, invoice.id, full_payment.id, actor_user_id=admin_user.id)

    from repositories.practice_repository import PracticeRepository

    reloaded = await PracticeRepository(db_session).get_by_id(practice.id)
    assert reloaded.collaborator_billing_status.value == "da_fatturare", "fattura/pagamento non devono toccare il flag collaboratore"

    billed = await practice_service.mark_collaborator_billed(db_session, practice.id, actor_user_id=admin_user.id)
    assert billed.collaborator_billing_status.value == "fatturato"

    # e viceversa: marcare il collaboratore come fatturato non deve
    # inventare/alterare nessuna fattura o pagamento esistente
    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "pagata"


async def test_settlement_duplication_case_practice_213(db_session, admin_user, sample_client, sample_location):
    """Regressione strutturale per il caso reale scoperto in V1 (pratica
    213/CR-000148, Fase 10C-10F): un incasso reale da 410,00 euro finiva
    rappresentato da DUE movimenti attivi nel ledger (820,00 euro invece
    di 410,00) a causa di un bug nella logica di correzione V1 (ricerca
    del movimento esistente per etichetta mutevole Saldo/Incasso completo,
    che poteva non trovarlo e crearne un secondo). V2 non ha quel pattern
    di ricerca-e-correggi (ogni correzione e' storno+nuovo inserimento,
    mai un UPDATE ne' una ricerca per etichetta) - questo test riproduce
    l'intero scenario end-to-end e verifica che l'invariante economica
    (pagato == reale, residuo == 0) regga in ogni fase, incluso il retry
    esatto della stessa richiesta (il gap che l'analisi ha trovato:
    register_payment generava una idempotency_key casuale ad ogni
    chiamata, quindi un retry di rete/doppio-click avrebbe potuto
    produrre lo stesso sintomo della pratica 213 per una via diversa)."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)

    async def active_settlements():
        from repositories.payment_repository import PaymentRepository

        payments = await PaymentRepository(db_session).list_for_practice(practice.id)
        reversed_ids = {p.related_payment_id for p in payments if p.movement_type == "Storno"}
        return [p for p in payments if p.movement_type in ("Saldo", "Incasso completo") and p.amount_cents > 0 and p.id not in reversed_ids]

    async def assert_invariant(expected_paid_cents):
        recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
        assert recon.paid_total_cents == expected_paid_cents
        assert recon.residual_cents == recon.effective_total_cents - recon.paid_total_cents
        assert recon.paid_total_cents + recon.residual_cents == recon.effective_total_cents

    # 1-2-3: pratica da 410,00 euro, un solo incasso reale registrato come
    # settlement (in V2 non esiste la coppia payment_movements/
    # balance_movements di V1: la riga payments E' gia' l'evento reale).
    settlement_key = "test-settlement-213:first-attempt"
    first = await payment_service.register_payment(
        db_session,
        _payment_data(practice.id, amount_cents=41000, movement_type="Incasso completo", idempotency_key=settlement_key),
        actor_user_id=admin_user.id,
    )
    assert first.amount_cents == 41000
    active = await active_settlements()
    assert len(active) == 1
    await assert_invariant(41000)

    # 4-5: ripetizione ESATTA della stessa richiesta (stessa chiave di
    # idempotenza) - deve restituire lo STESSO movimento, mai crearne un
    # secondo. Se questo fallisse, active_settlements() tornerebbe a
    # contenere 2 righe da 410 = 820 euro attivi: esattamente il sintomo
    # della pratica 213.
    retry = await payment_service.register_payment(
        db_session,
        _payment_data(practice.id, amount_cents=41000, movement_type="Incasso completo", idempotency_key=settlement_key),
        actor_user_id=admin_user.id,
    )
    assert retry.id == first.id, "un retry con la stessa idempotency_key deve restituire lo stesso movimento, non crearne uno nuovo"
    active = await active_settlements()
    assert len(active) == 1, f"atteso un solo settlement attivo dopo il retry, trovati: {[(p.id, p.movement_type, p.amount_cents) for p in active]}"
    await assert_invariant(41000)

    # 6: "modifica" del pagamento - in V2 non esiste un UPDATE sul ledger
    # (per design, e' esattamente il pattern che ha causato il bug V1):
    # la correzione e' sempre storno del movimento sbagliato + nuovo
    # movimento corretto, mai una ricerca-e-riscrittura in place.
    await payment_service.reverse_payment(db_session, first.id, "importo da correggere", actor_user_id=admin_user.id)
    active = await active_settlements()
    assert len(active) == 0, "dopo lo storno non deve restare nessun settlement attivo"
    await assert_invariant(0)

    corrected = await payment_service.register_payment(
        db_session,
        _payment_data(practice.id, amount_cents=41000, movement_type="Incasso completo", idempotency_key="test-settlement-213:corrected"),
        actor_user_id=admin_user.id,
    )
    active = await active_settlements()
    assert active == [corrected] or [p.id for p in active] == [corrected.id]
    await assert_invariant(41000)

    # 7: un secondo storno indipendente (sul movimento corretto) - deve
    # funzionare esattamente come il primo, nessuna logica parallela.
    await payment_service.reverse_payment(db_session, corrected.id, "annullamento", actor_user_id=admin_user.id)
    active = await active_settlements()
    assert len(active) == 0
    await assert_invariant(0)

    # 8: un nuovo pagamento reale, genuinamente indipendente.
    final_payment = await payment_service.register_payment(
        db_session,
        _payment_data(practice.id, amount_cents=41000, movement_type="Incasso completo", idempotency_key="test-settlement-213:final"),
        actor_user_id=admin_user.id,
    )
    active = await active_settlements()
    assert [p.id for p in active] == [final_payment.id]

    # 9-10: verifica finale - esattamente l'invariante richiesta:
    # 410 reali = 410 attivi = 410 pagati = 0 residui. Il test fallirebbe
    # se, per qualunque via, risultasse 820 (410 reali + 410 duplicati).
    recon = await payment_service.get_practice_reconciliation(db_session, practice.id)
    assert recon.effective_total_cents == 41000
    assert recon.paid_total_cents == 41000
    assert recon.residual_cents == 0
    assert recon.status == "pagata"
