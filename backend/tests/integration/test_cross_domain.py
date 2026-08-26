"""Fase 6 (doc12) - test cross-dominio: verificano che i domini V2 gia'
costruiti in questa sessione funzionino correttamente anche nelle loro
interazioni reciproche, non solo isolatamente. Ogni test qui tocca
ALMENO due domini gia' esistenti (Pratica, Ritiro, Riconsegna, Ciclo di
cremazione, Fatture/Pagamenti) e verifica un'invariante sui dati finali,
non solo che le chiamate rispondano.

Nota di ricostruzione (richiesta esplicitamente prima di scrivere questi
test): 'cestinazione' (trash_practice) in V2 e' SEMPRE un soft-delete
(deleted_at/deleted_by) - non esiste alcun percorso applicativo di
cancellazione fisica di una Pratica (per esplicita scelta doc06 '4.
Cancellazione pratica coerente': 'mai una DELETE reale nel flusso
normale'). Questo significa che cestinare una pratica NON valorizza mai
`invoices.practice_id`/`payments.practice_id` a NULL (la riga pratica
esiste ancora, solo soft-deleted) - il vincolo ON DELETE SET NULL
(doc06 righe 89/312, confermato dall'utente nel gate Fatture/Pagamenti)
e' una garanzia a livello database per un'EVENTUALE cancellazione fisica
futura, non ancora esercitata da alcun codice applicativo oggi. I test
sotto verificano quindi DUE cose distinte, entrambe reali:
1. il comportamento applicativo reale (cestinazione = la fattura resta
   invariata, practice_id continua a puntare alla pratica cestinata);
2. il vincolo del database stesso (verificato con una DELETE SQL diretta,
   che bypassa lo strato applicativo dato che non esiste ancora un
   servizio di cancellazione fisica) - garanzia gia' presente nello
   schema, qui solo confermata."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from domain.errors import ValidationDomainError
from models.calendar_event import CalendarEventType, DeliveryType, PickupStatus
from models.practice import PaymentChannel, PickupType
from repositories.audit_repository import AuditRepository
from repositories.calendar_event_repository import CalendarEventRepository
from repositories.invoice_repository import InvoiceRepository
from repositories.payment_repository import PaymentRepository
from repositories.practice_repository import PracticeRepository
from schemas.calendar_event import CreatePracticeFromPickupRequest, DeliveryCreate, LinkDeliveryToPracticeRequest, PickupCreate
from schemas.invoice import CorrectInvoiceTotalRequest, InvoiceCreate
from schemas.payment import PaymentCreate
from schemas.practice import AnimalInput, LineItemInput, OverrideTotalRequest, PracticeCreate, PracticeUpdate, TransitionRequest
from services import delivery_service, invoice_service, payment_service, pickup_service, practice_service


def _start_end():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return start, start + timedelta(hours=1)


async def _create_practice_with_total(db_session, admin_user, sample_client, sample_location, amount_cents):
    return await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            line_items=[LineItemInput(category="Cremazione", description="Cremazione singola", amount_cents=amount_cents)],
        ),
        actor_user_id=admin_user.id,
    )


# --- Pratica <-> Fatture/Pagamenti: cestinazione non deve mai toccare il dominio finanziario ---


async def test_invoice_and_payments_survive_direct_practice_trash(db_session, admin_user, sample_client, sample_location):
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 34000)
    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-XDOM-1", total_amount_cents=34000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )
    payment = await payment_service.register_payment(
        db_session,
        PaymentCreate(practice_id=practice.id, movement_date=date(2026, 1, 1), channel=PaymentChannel.W, ledger_section="Entrata", movement_type="Saldo", amount_cents=34000),
        actor_user_id=admin_user.id,
    )
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)

    recon_before = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon_before.status == "pagata"

    await practice_service.trash_practice(db_session, practice.id, "cestinata per test", actor_user_id=admin_user.id)

    # la pratica e' cestinata (soft-delete), NON cancellata fisicamente -
    # la fattura continua a puntarci correttamente, mai orfana ne' alterata
    trashed_practice = await PracticeRepository(db_session).get_by_id(practice.id, include_deleted=True)
    assert trashed_practice.deleted_at is not None

    reloaded_invoice = await InvoiceRepository(db_session).get_by_id(invoice.id)
    assert reloaded_invoice is not None, "la fattura non deve mai sparire per effetto della cestinazione"
    assert reloaded_invoice.practice_id == practice.id, "cestinare non è una DELETE - il collegamento resta valido"
    assert reloaded_invoice.total_amount_cents == 34000

    reloaded_payment = await PaymentRepository(db_session).get_by_id(payment.id)
    assert reloaded_payment is not None
    assert reloaded_payment.amount_cents == 34000

    recon_after = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon_after.status == "pagata", "la riconciliazione continua a funzionare identica dopo la cestinazione"


async def test_full_financial_lifecycle_survives_practice_trash(db_session, admin_user, sample_client, sample_location):
    """Scenario esplicitamente richiesto: fattura + pagamenti + storno +
    correzione del totale, poi cestinazione della pratica - l'intera
    storia finanziaria (pagamento originale, storno, fattura corretta)
    deve restare intatta e la riconciliazione deve continuare a
    calcolarsi correttamente contro il totale corretto."""
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 34000)
    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-XDOM-5", total_amount_cents=34000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )
    payment_a = await payment_service.register_payment(
        db_session,
        PaymentCreate(practice_id=practice.id, movement_date=date(2026, 1, 1), channel=PaymentChannel.W, ledger_section="Entrata", movement_type="Acconto", amount_cents=20000),
        actor_user_id=admin_user.id,
    )
    payment_b = await payment_service.register_payment(
        db_session,
        PaymentCreate(practice_id=practice.id, movement_date=date(2026, 1, 2), channel=PaymentChannel.W, ledger_section="Entrata", movement_type="Saldo", amount_cents=14000),
        actor_user_id=admin_user.id,
    )
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment_a.id, actor_user_id=admin_user.id)
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment_b.id, actor_user_id=admin_user.id)

    # Lo storno di uno dei due pagamenti (errore di registrazione)...
    reversal = await payment_service.reverse_payment(db_session, payment_b.id, "importo saldo errato", actor_user_id=admin_user.id)
    # ...seguito dalla correzione del totale fattura (scoperto uno sconto
    # concordato dopo l'emissione).
    corrected = await invoice_service.correct_invoice_total(
        db_session, invoice.id, CorrectInvoiceTotalRequest(total_amount_cents=20000, reason="sconto concordato dopo l'emissione"),
        actor_user_id=admin_user.id,
    )
    assert corrected.total_amount_cents == 20000

    recon_before_trash = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon_before_trash.status == "pagata"  # 20000 fattura, 20000 (solo payment_a) pagato

    await practice_service.trash_practice(db_session, practice.id, "test lifecycle finanziario completo", actor_user_id=admin_user.id)

    # Tutta la storia finanziaria resta leggibile e invariata dopo la cestinazione.
    final_a = await PaymentRepository(db_session).get_by_id(payment_a.id)
    final_b = await PaymentRepository(db_session).get_by_id(payment_b.id)
    final_reversal = await PaymentRepository(db_session).get_by_id(reversal.id)
    assert final_a.amount_cents == 20000
    assert final_b.amount_cents == 14000, "il pagamento stornato non viene mai cancellato, solo compensato"
    assert final_reversal.amount_cents == -14000
    assert final_reversal.related_payment_id == payment_b.id

    final_invoice = await InvoiceRepository(db_session).get_by_id(invoice.id)
    assert final_invoice.total_amount_cents == 20000, "la correzione resta valida dopo la cestinazione"

    recon_after_trash = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon_after_trash.status == "pagata"
    assert recon_after_trash.paid_cents == 20000, "lo storno resta escluso dal calcolo del pagato anche dopo la cestinazione"


async def test_invoice_survives_pickup_cancel_and_trash_practice_chain(
    db_session, admin_user, sample_client, sample_location, sample_zone
):
    """Catena completa Ritiro -> Pratica -> Fattura -> Pagamento, poi
    annullamento del Ritiro CON cestinazione della pratica collegata
    (Azione B, doc06 sezione 6/7) - verifica che l'intero stato
    finanziario sopravviva intatto."""
    start, end = _start_end()
    pickup = await pickup_service.create_pickup(
        db_session,
        PickupCreate(start_at=start, end_at=end, client_id=sample_client.id, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.da_ritirare, actor_user_id=admin_user.id)
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.ritirato, actor_user_id=admin_user.id)
    practice = await pickup_service.create_practice_from_pickup_action(
        db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
    )

    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-XDOM-2", total_amount_cents=10000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )
    payment = await payment_service.register_payment(
        db_session,
        PaymentCreate(practice_id=practice.id, movement_date=date(2026, 1, 1), channel=PaymentChannel.W, ledger_section="Entrata", movement_type="Acconto", amount_cents=4000),
        actor_user_id=admin_user.id,
    )
    await payment_service.link_payment_to_invoice(db_session, invoice.id, payment.id, actor_user_id=admin_user.id)

    cancelled = await pickup_service.cancel_pickup_and_trash_practice(
        db_session, pickup.id, "cliente ha annullato dopo il ritiro", actor_user_id=admin_user.id
    )
    assert cancelled.pickup_status == PickupStatus.annullato

    trashed_practice = await PracticeRepository(db_session).get_by_id(practice.id, include_deleted=True)
    assert trashed_practice.deleted_at is not None

    recon = await invoice_service.get_reconciliation(db_session, invoice.id)
    assert recon.status == "parziale"
    assert recon.paid_cents == 4000, "il pagamento gia' registrato resta intatto anche dopo l'annullamento del ritiro a monte"


async def test_hard_delete_of_practice_row_sets_invoice_and_payment_practice_id_null(
    db_session, admin_user, sample_client, sample_location
):
    """Verifica il vincolo ON DELETE SET NULL a livello database (doc06
    righe 89/312, confermato nel gate Fatture/Pagamenti) - nessun servizio
    applicativo esegue oggi una DELETE fisica su practices, quindi questo
    test esercita direttamente lo schema (DELETE SQL diretta) per
    confermare che la garanzia scritta nel modello dati sia realmente
    presente, non solo dichiarata nei commenti."""
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 20000)
    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-XDOM-3", total_amount_cents=20000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )
    payment = await payment_service.register_payment(
        db_session,
        PaymentCreate(practice_id=practice.id, movement_date=date(2026, 1, 1), channel=PaymentChannel.W, ledger_section="Entrata", movement_type="Saldo", amount_cents=20000),
        actor_user_id=admin_user.id,
    )
    practice_id = practice.id
    invoice_id = invoice.id
    payment_id = payment.id
    snapshot_number = practice.practice_number

    await db_session.execute(text("DELETE FROM practices WHERE id = :id"), {"id": practice_id})
    await db_session.commit()
    # La DELETE sopra e' un'istruzione Core (testuale), non un'operazione
    # ORM - la sessione non sa invalidare da sola gli oggetti gia' in
    # identity map (invoice/payment caricati prima, con practice_id
    # ancora valorizzato). expire_all() forza una rilettura reale dal
    # database sotto, altrimenti si osserverebbe il valore in cache anche
    # se ON DELETE SET NULL ha gia' agito correttamente a livello DB.
    db_session.expire_all()

    reloaded_invoice = await InvoiceRepository(db_session).get_by_id(invoice_id)
    assert reloaded_invoice is not None, "la fattura non deve mai sparire, nemmeno se la pratica viene davvero cancellata"
    assert reloaded_invoice.practice_id is None, "ON DELETE SET NULL deve azzerare il riferimento"
    assert reloaded_invoice.practice_number_snapshot == snapshot_number, "lo snapshot resta l'unico riferimento leggibile"

    reloaded_payment = await PaymentRepository(db_session).get_by_id(payment_id)
    assert reloaded_payment is not None
    assert reloaded_payment.practice_id is None
    assert reloaded_payment.practice_number_snapshot == snapshot_number


# --- Ritiro + Pratica: atomicita' dell'azione cross-dominio B (annulla + cestina) ---


async def test_cancel_pickup_and_trash_practice_atomicity(db_session, admin_user, sample_client, sample_location, sample_zone):
    """Replica manuale dei passi di cancel_pickup_and_trash_practice
    (che internamente delega a trash_practice, un solo commit fisico) -
    fallimento forzato nell'ultimo passo: NE' il ritiro NE' la pratica
    devono risultare modificati."""
    start, end = _start_end()
    pickup = await pickup_service.create_pickup(
        db_session,
        PickupCreate(start_at=start, end_at=end, client_id=sample_client.id, pickup_type=PickupType.domicilio, pickup_zone_id=sample_zone.id),
        actor_user_id=admin_user.id,
    )
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.da_ritirare, actor_user_id=admin_user.id)
    await pickup_service.transition_pickup(db_session, pickup.id, PickupStatus.ritirato, actor_user_id=admin_user.id)
    practice = await pickup_service.create_practice_from_pickup_action(
        db_session, pickup.id, CreatePracticeFromPickupRequest(destination_branch_id=sample_location.id), actor_user_id=admin_user.id
    )
    pickup_id = pickup.id
    practice_id = practice.id

    calendar_repo = CalendarEventRepository(db_session)
    practice_repo = PracticeRepository(db_session)
    audit = AuditRepository(db_session)

    reloaded_pickup = await calendar_repo.get_by_id(pickup_id)
    reloaded_pickup.pickup_status = PickupStatus.annullato
    audit.record(entity_type="calendar_event", entity_id=pickup_id, action="state_changed", user_id=admin_user.id)

    reloaded_practice = await practice_repo.get_by_id(practice_id)
    reloaded_practice.deleted_at = datetime.now(timezone.utc)
    reloaded_practice.deleted_by = admin_user.id
    # Fallimento forzato nell'ultimo passo (quello che in
    # trash_practice precede il commit).
    audit.record(entity_type=None, entity_id=practice_id, action="trashed", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final_pickup = await calendar_repo.get_by_id(pickup_id)
    assert final_pickup.pickup_status == PickupStatus.ritirato, "il ritiro non deve risultare annullato se la transazione fallisce"

    final_practice = await practice_repo.get_by_id(practice_id)
    assert final_practice is not None and final_practice.deleted_at is None, "la pratica non deve risultare cestinata se la transazione fallisce"


# --- Riconsegna + Pratica: riconciliazione contro il totale EFFETTIVO (override incluso) ---


async def test_delivery_mismatch_check_uses_override_not_raw_line_items_total(
    db_session, admin_user, sample_client, sample_location
):
    """Il totale 'effettivo' di una pratica (domain.practice.rules.
    effective_total_cents) e' l'override quando presente, non la somma
    preventivo - la riconciliazione Riconsegna deve usare la stessa
    identica fonte, mai un proprio calcolo parallelo."""
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 34000)
    await practice_service.set_total_override(
        db_session, practice.id, OverrideTotalRequest(amount_cents=20000, reason="sconto concordato"), actor_user_id=admin_user.id
    )

    start, end = _start_end()
    # L'importo preliminare coincide col vecchio totale preventivo (34000)
    # ma NON col totale effettivo post-override (20000) - deve essere
    # trattato come mismatch, non silenziosamente accettato.
    mismatched_delivery = await delivery_service.create_delivery(
        db_session,
        DeliveryCreate(start_at=start, end_at=end, delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=34000),
        actor_user_id=admin_user.id,
    )
    with pytest.raises(ValidationDomainError):
        await delivery_service.link_delivery_to_practice(
            db_session, mismatched_delivery.id, LinkDeliveryToPracticeRequest(practice_id=practice.id), actor_user_id=admin_user.id
        )

    # Un secondo importo preliminare che invece coincide col totale
    # EFFETTIVO (20000, l'override) deve collegarsi senza alcun mismatch.
    matching_delivery = await delivery_service.create_delivery(
        db_session,
        DeliveryCreate(start_at=start, end_at=end, delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=20000),
        actor_user_id=admin_user.id,
    )
    linked = await delivery_service.link_delivery_to_practice(
        db_session, matching_delivery.id, LinkDeliveryToPracticeRequest(practice_id=practice.id), actor_user_id=admin_user.id
    )
    assert linked.linked_practice_id == practice.id


async def test_link_delivery_to_practice_atomicity(db_session, admin_user, sample_client, sample_location):
    """Nessun test di atomicita' esisteva ancora per questa azione (unico
    punto di contatto scrittura tra i domini Riconsegna e Pratica) -
    fallimento forzato: la riconsegna non deve risultare collegata."""
    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 10000)
    start, end = _start_end()
    delivery = await delivery_service.create_delivery(
        db_session,
        DeliveryCreate(start_at=start, end_at=end, delivery_type=DeliveryType.sede_aziendale, delivery_location_id=sample_location.id, preliminary_payment_amount=10000),
        actor_user_id=admin_user.id,
    )
    delivery_id = delivery.id

    calendar_repo = CalendarEventRepository(db_session)
    reloaded = await calendar_repo.get_by_id(delivery_id)
    reloaded.linked_practice_id = practice.id
    AuditRepository(db_session).record(entity_type=None, entity_id=delivery_id, action="linked_to_practice", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final = await calendar_repo.get_by_id(delivery_id)
    assert final.linked_practice_id is None, "la riconsegna non deve risultare collegata se la transazione fallisce"


# --- Cestinazione/ripristino: i riferimenti cross-dominio restano intatti ---


async def test_restore_practice_preserves_invoice_and_cremation_cycle_links(
    db_session, admin_user, sample_client, sample_location
):
    from models.cremation_cycle import CremationCycle
    from schemas.cremation_cycle import CremationCycleCreate
    from services import cremation_cycle_service

    practice = await _create_practice_with_total(db_session, admin_user, sample_client, sample_location, 10000)
    # cremation_cycle_service.assign_animal richiede una pratica gia'
    # 'Cremazione singola' - _create_practice_with_total la crea gia' cosi'.
    updated = await practice_service.update_practice(
        db_session,
        practice.id,
        PracticeUpdate(
            destination_branch_id=practice.destination_branch_id,
            request_origin=practice.request_origin,
            service_type=practice.service_type,
            animals=[AnimalInput(name="Fido")],
            line_items=[LineItemInput(category="Cremazione", description="Cremazione singola", amount_cents=10000)],
        ),
        actor_user_id=admin_user.id,
    )
    await practice_service.transition_practice_state(
        db_session, practice.id, TransitionRequest(target_status="in_programma"), actor_user_id=admin_user.id
    )
    animal_id = updated.animals[0].id

    cycle = await cremation_cycle_service.create_cycle(
        db_session, CremationCycleCreate(cycle_date=date(2026, 9, 1), planned_start="09:00", planned_end="10:00"), actor_user_id=admin_user.id
    )
    await cremation_cycle_service.assign_animal(db_session, cycle.id, animal_id, actor_user_id=admin_user.id)

    invoice = await invoice_service.create_invoice(
        db_session,
        InvoiceCreate(practice_id=practice.id, invoice_number="FT-XDOM-4", total_amount_cents=10000, channel=PaymentChannel.W),
        actor_user_id=admin_user.id,
    )

    await practice_service.trash_practice(db_session, practice.id, "test ripristino", actor_user_id=admin_user.id)
    restored = await practice_service.restore_practice(db_session, practice.id, actor_user_id=admin_user.id)
    assert restored.deleted_at is None

    reloaded_invoice = await InvoiceRepository(db_session).get_by_id(invoice.id)
    assert reloaded_invoice.practice_id == practice.id, "il collegamento alla fattura resta intatto dopo cestinazione+ripristino"

    reloaded_cycle = await db_session.get(CremationCycle, cycle.id)
    assert any(a.id == animal_id for a in reloaded_cycle.animals), "l'assegnazione al ciclo di cremazione resta intatta dopo cestinazione+ripristino"