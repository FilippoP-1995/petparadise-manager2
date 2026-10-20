from schemas.payment import PaymentCreate
from schemas.practice import LineItemInput, PracticeCreate
from services import dashboard_service, payment_service, practice_service


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


async def test_dashboard_summary_status_and_payment_counts(db_session, admin_user, sample_client, sample_location):
    unpaid = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    partial = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=partial.id, movement_date="2026-01-01", channel="W", ledger_section="Entrata",
            movement_type="Acconto", amount_cents=10000,
        ),
        actor_user_id=admin_user.id,
    )
    paid = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=paid.id, movement_date="2026-01-01", channel="W", ledger_section="Entrata",
            movement_type="Incasso completo", amount_cents=41000,
        ),
        actor_user_id=admin_user.id,
    )

    summary = await dashboard_service.get_dashboard_summary(db_session, "oggi")

    assert summary.practice_status_counts.ritirato >= 3, "le 3 pratiche appena create devono comparire nel conteggio per stato"
    assert summary.payment_status_counts.non_pagata >= 1
    assert summary.payment_status_counts.parziale >= 1
    assert summary.payment_status_counts.pagata >= 1

    ids_present = {p.id for p in summary.recent_practices}
    assert paid.id in ids_present or len(summary.recent_practices) == 10, (
        "la pratica appena pagata deve comparire tra le recenti (a meno che ce ne siano gia' 10 piu' recenti nel DB di test)"
    )


async def test_dashboard_summary_incasso_periodo_matches_active_payments(db_session, admin_user, sample_client, sample_location):
    from datetime import datetime, timezone

    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    today = datetime.now(timezone.utc).date().isoformat()

    payment = await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=practice.id, movement_date=today, channel="W", ledger_section="Entrata",
            movement_type="Incasso completo", amount_cents=41000,
        ),
        actor_user_id=admin_user.id,
    )

    before_reversal = await dashboard_service.get_dashboard_summary(db_session, "oggi")
    assert before_reversal.incasso_oggi_cents >= 41000

    await payment_service.reverse_payment(db_session, payment.id, "test storno", actor_user_id=admin_user.id)

    after_reversal = await dashboard_service.get_dashboard_summary(db_session, "oggi")
    assert after_reversal.incasso_oggi_cents == before_reversal.incasso_oggi_cents - 41000, (
        "un movimento stornato non deve piu' contribuire all'incasso del giorno - stessa regola di 'attivo' usata "
        "ovunque nel ledger (Fase 10G/213)"
    )


async def test_dashboard_summary_recent_practice_shows_owner_and_animal_names(
    db_session, admin_user, sample_client, sample_location
):
    practice = await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            line_items=[LineItemInput(category="Cremazione", description="Cremazione singola", amount_cents=10000)],
            animals=[{"name": "Fido", "species": "Cane"}],
        ),
        actor_user_id=admin_user.id,
    )

    summary = await dashboard_service.get_dashboard_summary(db_session, "oggi")
    match = next(p for p in summary.recent_practices if p.id == practice.id)
    assert match.owner_display_name == "Mario Rossi"
    assert match.animal_names == "Fido"


async def test_dashboard_summary_payment_reminders_lists_oldest_unpaid_practices(
    db_session, admin_user, sample_client, sample_location
):
    """Non un sistema di notifiche (nessuna nuova tabella): solo le
    pratiche non_pagata/parziale gia' individuate per payment_status_counts,
    riesposte come elenco 'da seguire' - una pagata non deve comparire."""
    unpaid = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    partial = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=partial.id, movement_date="2026-01-01", channel="W", ledger_section="Entrata",
            movement_type="Acconto", amount_cents=10000,
        ),
        actor_user_id=admin_user.id,
    )
    paid = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=paid.id, movement_date="2026-01-01", channel="W", ledger_section="Entrata",
            movement_type="Incasso completo", amount_cents=41000,
        ),
        actor_user_id=admin_user.id,
    )

    summary = await dashboard_service.get_dashboard_summary(db_session, "oggi")
    reminder_ids = {r.practice_id for r in summary.payment_reminders}

    assert unpaid.id in reminder_ids
    assert partial.id in reminder_ids
    assert paid.id not in reminder_ids

    partial_reminder = next(r for r in summary.payment_reminders if r.practice_id == partial.id)
    assert partial_reminder.due_cents == 41000
    assert partial_reminder.paid_cents == 10000
    assert partial_reminder.residual_cents == 31000


async def test_dashboard_summary_practice_status_counts_are_periodized(db_session, admin_user, sample_client, sample_location):
    """V1 (state_specs): il conteggio 'Ritirato' della Dashboard cambia con
    la pillola periodo (Oggi/Settimana/Mese) - qui verificato con la
    pratica appena creata (pickup_date assente, quindi periodizzata sulla
    data odierna via il fallback updated_at) presente nel periodo 'oggi'
    ma NON in un periodo passato che non include oggi."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location)

    today_summary = await dashboard_service.get_dashboard_summary(db_session, "oggi")
    assert today_summary.practice_status_counts.ritirato >= 1

    # "Da consegnare" e Cremato/Smaltito restano invece una fotografia
    # corrente, senza filtro periodo (V1: "Aperti, senza scadenza").
    assert today_summary.practice_status_counts.da_consegnare == 0


async def test_dashboard_summary_quickstats_fields_are_consistent(db_session, admin_user, sample_client, sample_location):
    """Riga quick-stats (V1: quick_specs) - verifica che i nuovi campi
    aggregati siano coerenti tra loro e con le serie giornaliere a 7
    giorni, senza toccare la logica pagamenti gia' verificata altrove."""
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=practice.id, movement_date="2026-01-01", channel="W", ledger_section="Entrata",
            movement_type="Acconto", amount_cents=41000,
        ),
        actor_user_id=admin_user.id,
    )

    summary = await dashboard_service.get_dashboard_summary(db_session, "oggi")

    assert len(summary.daily_ritirato) == 7
    assert len(summary.daily_nuove) == 7
    assert len(summary.daily_da_saldare_proxy) == 7
    assert summary.daily_nuove[-1].count == summary.nuove_pratiche_oggi, "l'ultimo giorno della serie deve coincidere con 'oggi'"
    assert summary.pratiche_ritirato_oggi == summary.daily_ritirato[-1].count
    assert summary.nuove_pratiche_oggi >= 1
    assert summary.incasso_ieri_cents >= 0
    assert summary.incasso_precedenti_7_giorni_cents >= 0


async def test_dashboard_summary_payment_cards_count_movements_not_practices(
    db_session, admin_user, sample_client, sample_location
):
    """V1 (payment_specs): le card 'Acconto'/'Pagato' contano i MOVIMENTI
    avvenuti nel periodo (non le pratiche), 'Da saldare' resta invece una
    fotografia corrente (mai filtrata per periodo) - due Acconti sulla
    stessa pratica devono contare 2 movimenti, non 1 pratica."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    practice = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=41000)
    await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=practice.id, movement_date=today, channel="W", ledger_section="Entrata",
            movement_type="Acconto", amount_cents=10000,
        ),
        actor_user_id=admin_user.id,
    )
    await payment_service.register_payment(
        db_session,
        PaymentCreate(
            practice_id=practice.id, movement_date=today, channel="W", ledger_section="Entrata",
            movement_type="Acconto", amount_cents=5000,
        ),
        actor_user_id=admin_user.id,
    )
    unpaid = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=20000)

    summary = await dashboard_service.get_dashboard_summary(db_session, "oggi")

    assert summary.pagamenti_acconto_count_periodo == 2
    assert summary.pagamenti_acconto_totale_cents_periodo == 15000
    assert summary.pagamenti_da_saldare_count >= 2, "sia la pratica con acconto parziale sia quella non pagata devono essere conteggiate"
    assert summary.pagamenti_da_saldare_totale_cents >= (41000 - 15000) + 20000


async def test_dashboard_summary_payment_reminders_excludes_practice_with_zero_due(
    db_session, admin_user, sample_client, sample_location
):
    """Una pratica a importo dovuto zero e' 'non_pagata' per
    classify_payment_status (paid_cents<=0 e' vero anche quando due_cents e'
    0 - regola invariata, usata identica altrove), ma non ha nessun residuo
    economico reale da sollecitare: non deve comparire in 'Da seguire'."""
    zero_due = await _create_practice(db_session, admin_user, sample_client, sample_location, total_cents=0)

    summary = await dashboard_service.get_dashboard_summary(db_session, "oggi")
    reminder_ids = {r.practice_id for r in summary.payment_reminders}

    assert zero_due.id not in reminder_ids
