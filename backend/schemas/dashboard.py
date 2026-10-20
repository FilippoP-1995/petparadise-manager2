from datetime import date, datetime

from pydantic import BaseModel


class DashboardPracticeStatusCounts(BaseModel):
    """Stessi 6 stati di PracticeStatus (models/practice.py) - snapshot
    corrente (non filtrato per periodo), pratiche attive (deleted_at
    nullo)."""

    ritirato: int = 0
    in_programma: int = 0
    cremato: int = 0
    da_consegnare: int = 0
    consegnato: int = 0
    smaltito: int = 0


class DashboardPaymentStatusCounts(BaseModel):
    """Stessi 4 stati di domain.invoice.rules.classify_payment_status,
    riusata as-is (mai una seconda formula) per classificare ogni pratica
    attiva in base al suo effective_total_cents (override o somma
    line_items) contro quanto risulta realmente pagato sul ledger."""

    non_pagata: int = 0
    parziale: int = 0
    pagata: int = 0
    sovrapagata: int = 0


class DashboardDailyIncasso(BaseModel):
    date: date
    amount_cents: int


class DashboardDailyCount(BaseModel):
    date: date
    count: int


class DashboardRecentPractice(BaseModel):
    id: int
    practice_number: str
    status: str
    created_at: datetime
    owner_display_name: str
    animal_names: str
    # V1 (species_avatar): specie del primo animale - determina avatar/
    # bordo colorato della recent-practice-card, None se sconosciuta.
    primary_species: str | None = None


class DashboardPaymentReminder(BaseModel):
    """Non e' un sistema di notifiche/promemoria (nessun nuovo dominio,
    nessuna tabella nuova): sono le pratiche 'non_pagata'/'parziale' piu'
    vecchie, calcolate dagli stessi dati gia' aggregati per
    payment_status_counts - un elenco 'da seguire', non un vero sistema
    di notifiche push."""

    practice_id: int
    practice_number: str
    created_at: datetime
    due_cents: int
    paid_cents: int
    residual_cents: int


class DashboardSummaryRead(BaseModel):
    period: str
    # V1 (dashboard(): counts_by_period[practice_period]) - filtrati per il
    # periodo selezionato, non piu' uno snapshot fisso indipendente dalla
    # pillola Oggi/Settimana/Mese.
    practice_status_counts: DashboardPracticeStatusCounts
    payment_status_counts: DashboardPaymentStatusCounts
    incasso_periodo_cents: int
    incasso_oggi_cents: int
    nuove_pratiche_oggi: int
    daily_incassi: list[DashboardDailyIncasso]
    recent_practices: list[DashboardRecentPractice]
    payment_reminders: list[DashboardPaymentReminder]
    # Sezione Analytics (V1: incassi_chart_html) - stesso confronto "vs 7
    # giorni precedenti" gia' presente in V1.
    incasso_precedenti_7_giorni_cents: int
    # Riga quick-stats (V1: quick_specs) - stesse 4 metriche/confronto "vs
    # ieri" di V1, "Da saldare" con lo stesso identico proxy che V1 stesso
    # documenta come tale (conteggio per data di creazione, non un vero
    # storico del residuo).
    incasso_ieri_cents: int
    nuove_pratiche_ieri: int
    pratiche_ritirato_oggi: int
    pratiche_ritirato_ieri: int
    pratiche_da_saldare_oggi: int
    pratiche_da_saldare_ieri: int
    daily_ritirato: list[DashboardDailyCount]
    daily_nuove: list[DashboardDailyCount]
    daily_da_saldare_proxy: list[DashboardDailyCount]
    # V1 (payment_specs, app.py:8697-8715): le card "Pagamenti" del cruscotto
    # contano MOVIMENTI avvenuti nel periodo (Acconto/Saldo), non pratiche
    # classificate per stato - "Da saldare" resta invece una fotografia
    # corrente (tutte le rimanenze aperte ORA), non filtrata per periodo.
    # payment_status_counts resta invariato sopra (usato da altre viste),
    # questi campi aggiuntivi permettono alla Dashboard di mostrare le
    # stesse identiche 3 card + Totale di V1.
    pagamenti_da_saldare_count: int
    pagamenti_da_saldare_totale_cents: int
    pagamenti_acconto_count_periodo: int
    pagamenti_acconto_totale_cents_periodo: int
    pagamenti_pagato_count_periodo: int
    pagamenti_pagato_totale_cents_periodo: int
