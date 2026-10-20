"""Riepilogo aggregato per la Dashboard V2 (priorita' 1, richiesta
esplicita: la V2 non ne aveva una, la root '/' portava all'elenco
pratiche). Solo query di sola lettura, nessuna nuova regola economica:
il conteggio per stato pagamento riusa domain.invoice.rules.
classify_payment_status (la stessa gia' usata per la riconciliazione
per-fattura/per-pratica), e la definizione di 'movimento attivo' riusa
esattamente lo stesso filtro di PaymentRepository.sum_paid_for_practice_channel
(Entrata, amount_cents>0, non stornato) - nessuna logica parallela."""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.invoice.rules import classify_payment_status
from models.animal import Animal
from models.payment import LedgerSection, Payment
from models.practice import Practice, PracticeLineItem, PracticeStatus
from schemas.dashboard import (
    DashboardDailyCount,
    DashboardDailyIncasso,
    DashboardPaymentReminder,
    DashboardPaymentStatusCounts,
    DashboardPracticeStatusCounts,
    DashboardRecentPractice,
    DashboardSummaryRead,
)

# V1 (dashboard_practice_date_sql) determina la data di un evento di stato
# da colonne di transizione dedicate che V2 non ha (nessuna nuova colonna
# aggiunta qui, fase di sola parita' visiva): stesso fallback che V1 stesso
# usa quando quelle colonne mancano (COALESCE(pickup_date, date(updated_at))
# per gli stati "raggiunti", COALESCE(pickup_date, date(created_at)) per
# 'in programma') - non un valore inventato, la stessa tecnica di V1.
_RITIRATO_CLASS = (
    PracticeStatus.ritirato,
    PracticeStatus.cremato,
    PracticeStatus.da_consegnare,
    PracticeStatus.consegnato,
    PracticeStatus.smaltito,
)

MAX_PAYMENT_REMINDERS = 5

VALID_PERIODS = ("oggi", "settimana", "mese")


def _period_bounds(period: str, today: date) -> tuple[date, date]:
    if period == "settimana":
        return today - timedelta(days=today.weekday()), today
    if period == "mese":
        return today.replace(day=1), today
    return today, today


def _utc_date(column):
    """CAST(timestamptz AS date) in Postgres usa il TimeZone di sessione
    (qui Europe/Berlin), non UTC - un cast diretto sfaserebbe di un giorno
    ogni notte confrontato con 'today' calcolato in Python via
    datetime.now(timezone.utc) (bug reale, scoperto da un test che falliva
    proprio nell'ora di disallineamento). AT TIME ZONE 'UTC' normalizza
    prima del cast, rendendolo indipendente dal TimeZone di sessione."""
    return cast(func.timezone("UTC", column), Date)


def _active_entrata_filter(query, start: date | None = None, end: date | None = None):
    reversed_target_ids = select(Payment.related_payment_id).where(
        Payment.movement_type == "Storno", Payment.related_payment_id.isnot(None)
    )
    query = query.where(
        Payment.ledger_section == LedgerSection.entrata,
        Payment.amount_cents > 0,
        Payment.id.notin_(reversed_target_ids),
    )
    if start is not None:
        query = query.where(Payment.movement_date >= start)
    if end is not None:
        query = query.where(Payment.movement_date <= end)
    return query


async def get_dashboard_summary(db: AsyncSession, period: str) -> DashboardSummaryRead:
    if period not in VALID_PERIODS:
        period = "oggi"
    today = datetime.now(timezone.utc).date()
    start, end = _period_bounds(period, today)

    reached_date = func.coalesce(Practice.pickup_date, _utc_date(Practice.updated_at))
    programma_date = func.coalesce(Practice.pickup_date, _utc_date(Practice.created_at))
    yesterday = today - timedelta(days=1)

    async def _count_ritirato_class(day_start: date, day_end: date) -> int:
        return (
            await db.execute(
                select(func.count())
                .select_from(Practice)
                .where(Practice.deleted_at.is_(None), Practice.status.in_(_RITIRATO_CLASS), reached_date.between(day_start, day_end))
            )
        ).scalar_one()

    async def _count_status(status: PracticeStatus, date_expr, day_start: date, day_end: date) -> int:
        return (
            await db.execute(
                select(func.count())
                .select_from(Practice)
                .where(Practice.deleted_at.is_(None), Practice.status == status, date_expr.between(day_start, day_end))
            )
        ).scalar_one()

    # V1 (state_specs, app.py:8663-8669): "Ritirato"/"In programma"/
    # "Consegnato" cambiano con la pillola periodo, "Da consegnare" e'
    # sempre la fotografia corrente (nessun filtro data, richiesta esplicita
    # V1: "Aperti, senza scadenza"). Cremato/Smaltito non compaiono affatto
    # nella Dashboard V1: qui restano uno snapshot corrente, invariati.
    pratiche_ritirato_periodo = await _count_ritirato_class(start, end)
    pratiche_in_programma_periodo = await _count_status(PracticeStatus.in_programma, programma_date, start, end)
    pratiche_consegnato_periodo = await _count_status(PracticeStatus.consegnato, reached_date, start, end)
    snapshot_rows = (
        await db.execute(
            select(Practice.status, func.count())
            .where(
                Practice.deleted_at.is_(None),
                Practice.status.in_((PracticeStatus.da_consegnare, PracticeStatus.cremato, PracticeStatus.smaltito)),
            )
            .group_by(Practice.status)
        )
    ).all()
    snapshot_by_status = {s.value: n for s, n in snapshot_rows}
    practice_status_counts = DashboardPracticeStatusCounts(
        ritirato=pratiche_ritirato_periodo,
        in_programma=pratiche_in_programma_periodo,
        cremato=snapshot_by_status.get("cremato", 0),
        da_consegnare=snapshot_by_status.get("da_consegnare", 0),
        consegnato=pratiche_consegnato_periodo,
        smaltito=snapshot_by_status.get("smaltito", 0),
    )

    incasso_periodo = (await db.execute(_active_entrata_filter(select(func.coalesce(func.sum(Payment.amount_cents), 0)), start, end))).scalar_one()
    incasso_oggi = (await db.execute(_active_entrata_filter(select(func.coalesce(func.sum(Payment.amount_cents), 0)), today, today))).scalar_one()
    incasso_ieri = (await db.execute(_active_entrata_filter(select(func.coalesce(func.sum(Payment.amount_cents), 0)), yesterday, yesterday))).scalar_one()

    seven_days_ago = today - timedelta(days=6)
    fourteen_days_ago = today - timedelta(days=13)
    eight_days_ago = today - timedelta(days=8)
    daily_rows = (
        await db.execute(
            _active_entrata_filter(
                select(Payment.movement_date, func.sum(Payment.amount_cents)).group_by(Payment.movement_date),
                seven_days_ago,
                today,
            )
        )
    ).all()
    by_day = {d: cents for d, cents in daily_rows}
    daily_incassi = [
        DashboardDailyIncasso(date=seven_days_ago + timedelta(days=i), amount_cents=by_day.get(seven_days_ago + timedelta(days=i), 0))
        for i in range(7)
    ]
    # V1 (incassi_delta_pct vs 'incassi_prev_total'): confronto con i 7
    # giorni immediatamente precedenti alla finestra mostrata dal grafico.
    incasso_precedenti_7_giorni = (
        await db.execute(_active_entrata_filter(select(func.coalesce(func.sum(Payment.amount_cents), 0)), fourteen_days_ago, eight_days_ago))
    ).scalar_one()

    async def _daily_ritirato_class() -> dict[date, int]:
        rows = (
            await db.execute(
                select(reached_date, func.count())
                .select_from(Practice)
                .where(Practice.deleted_at.is_(None), Practice.status.in_(_RITIRATO_CLASS), reached_date.between(seven_days_ago, today))
                .group_by(reached_date)
            )
        ).all()
        return {d: n for d, n in rows}

    ritirato_by_day = await _daily_ritirato_class()
    daily_ritirato = [
        DashboardDailyCount(date=seven_days_ago + timedelta(days=i), count=ritirato_by_day.get(seven_days_ago + timedelta(days=i), 0))
        for i in range(7)
    ]
    pratiche_ritirato_oggi = ritirato_by_day.get(today, 0)
    pratiche_ritirato_ieri = await _count_ritirato_class(yesterday, yesterday)

    due_rows = (
        await db.execute(
            select(Practice.id, Practice.computed_total_override_cents, func.coalesce(func.sum(PracticeLineItem.amount_cents), 0))
            .outerjoin(PracticeLineItem, PracticeLineItem.practice_id == Practice.id)
            .where(Practice.deleted_at.is_(None))
            .group_by(Practice.id, Practice.computed_total_override_cents)
        )
    ).all()
    paid_rows = (
        await db.execute(
            _active_entrata_filter(select(Payment.practice_id, func.sum(Payment.amount_cents)).group_by(Payment.practice_id)).where(
                Payment.practice_id.isnot(None)
            )
        )
    ).all()
    paid_by_practice = {pid: cents for pid, cents in paid_rows}
    payment_counts = {"non_pagata": 0, "parziale": 0, "pagata": 0, "sovrapagata": 0}
    unpaid_practice_ids: dict[int, tuple[int, int]] = {}
    for practice_id, override_cents, line_items_sum in due_rows:
        due_cents = override_cents if override_cents is not None else line_items_sum
        paid_cents = paid_by_practice.get(practice_id, 0)
        status = classify_payment_status(due_cents, paid_cents)
        payment_counts[status] += 1
        # "Da seguire" e' un elenco di residui da incassare: una pratica a
        # importo dovuto zero e' "non_pagata" per classify_payment_status
        # (paid_cents<=0 e' sempre vero quando due_cents e' anch'esso 0 -
        # la regola resta corretta e invariata, e' usata identica altrove
        # per classificare fatture/pratiche reali), ma non ha nessun
        # residuo economico da sollecitare: esclusa qui, non dal conteggio
        # payment_status_counts sopra (quello riflette lo stato reale della
        # pratica, non solo l'elenco "da seguire").
        if status in ("non_pagata", "parziale") and due_cents > 0:
            unpaid_practice_ids[practice_id] = (due_cents, paid_cents)
    payment_status_counts = DashboardPaymentStatusCounts(**payment_counts)

    # V1 (payment_specs, app.py:8697-8715): "Da saldare" e' la stessa
    # fotografia corrente di unpaid_practice_ids (mai filtrata per
    # periodo), "Acconto"/"Pagato" contano invece i MOVIMENTI (non le
    # pratiche) avvenuti nel periodo selezionato.
    pagamenti_da_saldare_count = len(unpaid_practice_ids)
    pagamenti_da_saldare_totale_cents = sum(due - paid for due, paid in unpaid_practice_ids.values())
    period_movement_rows = (
        await db.execute(
            _active_entrata_filter(select(Payment.movement_type, func.count(), func.sum(Payment.amount_cents)), start, end)
            .group_by(Payment.movement_type)
        )
    ).all()
    pagamenti_acconto_count_periodo = 0
    pagamenti_acconto_totale_cents_periodo = 0
    pagamenti_pagato_count_periodo = 0
    pagamenti_pagato_totale_cents_periodo = 0
    for movement_type, count, total_cents in period_movement_rows:
        if movement_type == "Acconto":
            pagamenti_acconto_count_periodo = count
            pagamenti_acconto_totale_cents_periodo = total_cents or 0
        elif movement_type in ("Saldo", "Incasso completo"):
            pagamenti_pagato_count_periodo += count
            pagamenti_pagato_totale_cents_periodo += total_cents or 0

    # V1 (quick_specs "Da saldare", commento originale: "e' una fotografia
    # delle rimanenze aperte ORA, non un flusso: come proxy per la
    # sparkline conto quante di queste pratiche sono state create in
    # ciascuno degli ultimi 7 giorni") - stesso identico proxy, non una
    # nuova approssimazione inventata qui.
    da_saldare_by_day: dict[date, int] = {}
    if unpaid_practice_ids:
        unpaid_created_rows = (
            await db.execute(select(Practice.id, _utc_date(Practice.created_at)).where(Practice.id.in_(unpaid_practice_ids.keys())))
        ).all()
        for _practice_id, created_date in unpaid_created_rows:
            if created_date >= seven_days_ago:
                da_saldare_by_day[created_date] = da_saldare_by_day.get(created_date, 0) + 1
    daily_da_saldare_proxy = [
        DashboardDailyCount(date=seven_days_ago + timedelta(days=i), count=da_saldare_by_day.get(seven_days_ago + timedelta(days=i), 0))
        for i in range(7)
    ]
    pratiche_da_saldare_oggi = da_saldare_by_day.get(today, 0)
    pratiche_da_saldare_ieri = da_saldare_by_day.get(yesterday, 0)

    # "Promemoria" pagamenti: non un nuovo dominio/tabella (nessuna
    # migrazione) - solo le pratiche non_pagata/parziale gia' individuate
    # sopra, ordinate dalla piu' vecchia, un sottoinsieme utile per
    # l'operatore senza costruire un sistema di notifiche separato.
    payment_reminders: list[DashboardPaymentReminder] = []
    if unpaid_practice_ids:
        reminder_rows = (
            await db.execute(
                select(Practice.id, Practice.practice_number, Practice.created_at)
                .where(Practice.id.in_(unpaid_practice_ids.keys()))
                .order_by(Practice.created_at.asc())
                .limit(MAX_PAYMENT_REMINDERS)
            )
        ).all()
        for practice_id, practice_number, created_at in reminder_rows:
            due_cents, paid_cents = unpaid_practice_ids[practice_id]
            payment_reminders.append(
                DashboardPaymentReminder(
                    practice_id=practice_id,
                    practice_number=practice_number,
                    created_at=created_at,
                    due_cents=due_cents,
                    paid_cents=paid_cents,
                    residual_cents=due_cents - paid_cents,
                )
            )

    nuove_oggi = (
        await db.execute(
            select(func.count()).select_from(Practice).where(Practice.deleted_at.is_(None), _utc_date(Practice.created_at) == today)
        )
    ).scalar_one()
    nuove_ieri = (
        await db.execute(
            select(func.count()).select_from(Practice).where(Practice.deleted_at.is_(None), _utc_date(Practice.created_at) == yesterday)
        )
    ).scalar_one()
    created_date = _utc_date(Practice.created_at)
    nuove_rows = (
        await db.execute(
            select(created_date, func.count())
            .select_from(Practice)
            .where(Practice.deleted_at.is_(None), created_date.between(seven_days_ago, today))
            .group_by(created_date)
        )
    ).all()
    nuove_by_day = {d: n for d, n in nuove_rows}
    daily_nuove = [
        DashboardDailyCount(date=seven_days_ago + timedelta(days=i), count=nuove_by_day.get(seven_days_ago + timedelta(days=i), 0))
        for i in range(7)
    ]

    recent = (
        await db.execute(select(Practice).where(Practice.deleted_at.is_(None)).order_by(Practice.created_at.desc()).limit(10))
    ).scalars().all()
    recent_ids = [p.id for p in recent]
    animal_rows = (
        (await db.execute(select(Animal.practice_id, Animal.name, Animal.species).where(Animal.practice_id.in_(recent_ids)))).all()
        if recent_ids
        else []
    )
    animals_by_practice: dict[int, list[str]] = {}
    species_by_practice: dict[int, str] = {}
    for practice_id, name, species in animal_rows:
        animals_by_practice.setdefault(practice_id, []).append(name or "")
        if practice_id not in species_by_practice and species:
            species_by_practice[practice_id] = species

    def _owner_name(snapshot: dict | None) -> str:
        if not snapshot:
            return ""
        if snapshot.get("company_name"):
            return snapshot["company_name"]
        return " ".join(part for part in (snapshot.get("first_name"), snapshot.get("last_name")) if part).strip()

    recent_practices = [
        DashboardRecentPractice(
            id=p.id,
            practice_number=p.practice_number,
            status=p.status.value,
            created_at=p.created_at,
            owner_display_name=_owner_name(p.owner_snapshot),
            animal_names=", ".join(n for n in animals_by_practice.get(p.id, []) if n),
            primary_species=species_by_practice.get(p.id),
        )
        for p in recent
    ]

    return DashboardSummaryRead(
        period=period,
        practice_status_counts=practice_status_counts,
        payment_status_counts=payment_status_counts,
        incasso_periodo_cents=incasso_periodo,
        incasso_oggi_cents=incasso_oggi,
        nuove_pratiche_oggi=nuove_oggi,
        daily_incassi=daily_incassi,
        recent_practices=recent_practices,
        payment_reminders=payment_reminders,
        incasso_precedenti_7_giorni_cents=incasso_precedenti_7_giorni,
        incasso_ieri_cents=incasso_ieri,
        nuove_pratiche_ieri=nuove_ieri,
        pratiche_ritirato_oggi=pratiche_ritirato_oggi,
        pratiche_ritirato_ieri=pratiche_ritirato_ieri,
        pratiche_da_saldare_oggi=pratiche_da_saldare_oggi,
        pratiche_da_saldare_ieri=pratiche_da_saldare_ieri,
        daily_ritirato=daily_ritirato,
        daily_nuove=daily_nuove,
        daily_da_saldare_proxy=daily_da_saldare_proxy,
        pagamenti_da_saldare_count=pagamenti_da_saldare_count,
        pagamenti_da_saldare_totale_cents=pagamenti_da_saldare_totale_cents,
        pagamenti_acconto_count_periodo=pagamenti_acconto_count_periodo,
        pagamenti_acconto_totale_cents_periodo=pagamenti_acconto_totale_cents_periodo,
        pagamenti_pagato_count_periodo=pagamenti_pagato_count_periodo,
        pagamenti_pagato_totale_cents_periodo=pagamenti_pagato_totale_cents_periodo,
    )
