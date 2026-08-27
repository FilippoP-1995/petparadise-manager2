import enum
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.base import TimestampMixin, pg_enum as _pg_enum


class PracticeStatus(str, enum.Enum):
    """doc06 'Stati e macchine a stati' + doc14 §1 - enum chiuso, workflow
    lineare validato in domain/practice/state_machine.py, mai un valore
    libero accettato dal database o dall'applicazione."""

    ritirato = "ritirato"
    in_programma = "in_programma"
    cremato = "cremato"
    da_consegnare = "da_consegnare"
    consegnato = "consegnato"
    smaltito = "smaltito"


class PickupType(str, enum.Enum):
    """doc06 Addendum C (riscritto) - nessuna logica di fallback: il tipo
    e' sempre scelto esplicitamente dall'operatore."""

    sede_aziendale = "sede_aziendale"
    domicilio = "domicilio"
    veterinario = "veterinario"
    collaboratore = "collaboratore"
    altro = "altro"


class PaymentChannel(str, enum.Enum):
    """doc06 '2. Pagamenti: un solo ledger, circuito mai ambiguo' - ogni
    riga di preventivo dichiara esplicitamente il proprio circuito, mai
    dedotto da quale campo e' valorizzato (il bug reale gia' trovato in V1)."""

    W = "W"
    D = "D"
    collaboratori = "Collaboratori"


class CollaboratorBillingStatus(str, enum.Enum):
    """doc06 Addendum F - flag di processo interno, esplicitamente separato
    dal documento fiscale (invoices)."""

    da_fatturare = "da_fatturare"
    fatturato = "fatturato"


class OwnerNotifiedStatus(str, enum.Enum):
    """doc06 Addendum G - stato corrente interrogabile direttamente,
    distinto dallo storico in audit_log."""

    da_avvisare = "da_avvisare"
    avvisato = "avvisato"


class PracticeNumberCounter(Base):
    """Sostituisce la riga generica in `settings` che V1 usa per lo stesso
    scopo (app.py:1316-1335, next_number/practice_code_prefix - FACT
    verificato) con una tabella dedicata e tipizzata. Stessa identica
    logica di business (contatore sequenziale per prefisso), struttura
    piu' pulita - non un algoritmo nuovo."""

    __tablename__ = "practice_number_counters"

    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Practice(TimestampMixin, Base):
    """doc06 struttura PRATICA completa + Addenda A-I (chiusura Architecture
    Gate). Vedi commenti inline solo dove il motivo non e' gia' ovvio dal
    nome colonna (rispecchia 1:1 doc06, non serve ripetere la motivazione
    di ogni singolo campo gia' scritta li')."""

    __tablename__ = "practices"
    __table_args__ = (
        # Release hardening (gate 6): dimostrato con EXPLAIN ANALYZE su un
        # dataset sintetico di 30k righe (~163x piu' veloce sulla lista
        # senza filtri, ~25x su quella filtrata per stato - non aggiunti
        # solo perche' "mancavano"). Due indici distinti (non uno solo con
        # status incluso sempre) perche' la lista senza filtro di stato e'
        # il caso piu' comune (nessun filtro attivo di default).
        Index("ix_practices_active_created", text("created_at DESC"), postgresql_where=text("deleted_at IS NULL")),
        Index(
            "ix_practices_active_status_created",
            "status",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    practice_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    status: Mapped[PracticeStatus] = mapped_column(
        _pg_enum(PracticeStatus, "practice_status"), nullable=False, default=PracticeStatus.ritirato
    )
    request_origin: Mapped[str] = mapped_column(String(30), nullable=False)

    # RISOLTO (era TEMPORARY CROSS-DOMAIN CONSTRAINT nella migrazione
    # 7598b50714a9, quando calendar_events non esisteva ancora): il dominio
    # Ritiro e' ora costruito (migrazione successiva) - FK reale applicata,
    # con i 4 passi pianificati eseguiti: 1) nessun dato esistente da
    # verificare (nessuna pratica aveva questo campo valorizzato, Percorso A
    # non era mai stato raggiungibile); 2) nessun riferimento orfano
    # possibile per lo stesso motivo; 3-4) FK aggiunta con ON DELETE SET
    # NULL (mai CASCADE/RESTRICT - la pratica non deve mai sparire ne'
    # essere bloccata dalla sorte del ritiro che l'ha generata).
    originating_pickup_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("calendar_events.id", ondelete="SET NULL")
    )

    destination_branch_id: Mapped[int] = mapped_column(ForeignKey("company_locations.id"), nullable=False)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False)
    service_type: Mapped[str] = mapped_column(String(30), nullable=False)
    collaborator_id: Mapped[int | None] = mapped_column(ForeignKey("collaborators.id"))
    veterinarian_id: Mapped[int | None] = mapped_column(ForeignKey("veterinarians.id"))
    origin_veterinarian_id: Mapped[int | None] = mapped_column(ForeignKey("veterinarians.id"))

    # RIMOSSO (era qui come TEMPORARY CROSS-DOMAIN CONSTRAINT): il Gate
    # Animali<->Cicli (round 2) ha chiuso la decisione aziendale sulla
    # granularita' dell'assegnazione al ciclo di cremazione a favore del
    # livello ANIMALE, non PRATICA - animals.cremation_cycle_id e' ora
    # l'unica fonte di verita' (vedi models/animal.py). Questo campo non
    # e' mai stato letto/scritto da alcun service (verificato prima di
    # rimuoverlo) - nessun refactor di logica esistente necessario.

    pickup_date: Mapped[date | None] = mapped_column(Date)
    pickup_time: Mapped[time | None] = mapped_column(Time)
    pickup_address: Mapped[str | None] = mapped_column(Text)
    microchip: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    ddt_number: Mapped[int | None] = mapped_column(Integer, unique=True)
    ddt_date: Mapped[date | None] = mapped_column(Date)
    ddt_pdf_path: Mapped[str | None] = mapped_column(Text)
    signature_data: Mapped[str | None] = mapped_column(Text)
    data_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Addendum A - owner snapshot storico
    # scritto UNA VOLTA alla creazione, mai piu' riscritto (regola esplicita
    # ricevuta in questa fase - vedi domain/practice/rules.py). Distinto da
    # clients: mai una seconda fonte di verita' dell'anagrafica corrente.
    owner_snapshot: Mapped[dict | None] = mapped_column(JSONB)

    # Addendum B - DDT / trasporto / tracciabilita' (VERIFICA NORMATIVA
    # PENDENTE, doc06 - conservazione indefinita come scelta prudenziale)
    transport_method: Mapped[str | None] = mapped_column(String(100))
    vehicle_plate: Mapped[str | None] = mapped_column(String(20))
    temperature_mode: Mapped[str | None] = mapped_column(String(60))
    package_count: Mapped[int | None] = mapped_column(Integer)
    container_id: Mapped[str | None] = mapped_column(String(100))
    lot_number: Mapped[str | None] = mapped_column(String(100))
    treatment_method: Mapped[str | None] = mapped_column(String(100))
    delivery_at_clinic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delivery_at_home: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signatory_identity_document_number: Mapped[str | None] = mapped_column(String(60))
    signatory_identity_document_date: Mapped[date | None] = mapped_column(Date)
    signatory_signing_place: Mapped[str | None] = mapped_column(String(120))
    ddt_share_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    original_practice_number: Mapped[str | None] = mapped_column(String(32))

    # Addendum C - logistica multi-sede (affido/ritiro)
    pickup_type: Mapped[PickupType] = mapped_column(
        _pg_enum(PickupType, "pickup_type"), nullable=False, default=PickupType.domicilio
    )
    pickup_location_id: Mapped[int | None] = mapped_column(ForeignKey("company_locations.id"))
    pickup_zone_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_zones.id"))
    pickup_contact_name: Mapped[str | None] = mapped_column(String(160))
    provenance_code: Mapped[str | None] = mapped_column(String(30))

    # Addendum D - override manuali (decisioni operatore, non dati derivati)
    computed_total_override_cents: Mapped[int | None] = mapped_column(BigInteger)
    computed_total_override_reason: Mapped[str | None] = mapped_column(Text)
    computed_total_override_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    computed_total_override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    to_invoice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Addendum E - workflow operativo
    send_catalog: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    catalog_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    send_estremi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estremi_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voucher_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    use_voucher: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whatsapp_thanks_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    whatsapp_thanks_last_error: Mapped[str | None] = mapped_column(Text)
    no_whatsapp_message: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cremation_registered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cremation_queued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Addendum F - collaboratori: fatturazione interna vs documento fiscale
    collaborator_billing_status: Mapped[CollaboratorBillingStatus] = mapped_column(
        _pg_enum(CollaboratorBillingStatus, "collaborator_billing_status"),
        nullable=False,
        default=CollaboratorBillingStatus.da_fatturare,
    )
    collaborator_billing_invoiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collaborator_name_fallback: Mapped[str | None] = mapped_column(String(200))

    # Addendum G - notifica proprietario cremazione
    owner_notified_status: Mapped[OwnerNotifiedStatus] = mapped_column(
        _pg_enum(OwnerNotifiedStatus, "owner_notified_status"), nullable=False, default=OwnerNotifiedStatus.da_avvisare
    )
    owner_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_notified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # Addendum I - voucher veterinario.
    # TEMPORARY CROSS-DOMAIN CONSTRAINT (doc06 REFERENCES
    # veterinarian_vouchers(id) ON DELETE SET NULL): stesso trattamento di
    # originating_pickup_event_id sopra - dominio Voucher non ancora
    # costruito.
    used_voucher_id: Mapped[int | None] = mapped_column(Integer)

    animals: Mapped[list["Animal"]] = relationship(
        "Animal", cascade="all, delete-orphan", order_by="Animal.sort_order"
    )
    line_items: Mapped[list["PracticeLineItem"]] = relationship(
        "PracticeLineItem", cascade="all, delete-orphan", order_by="PracticeLineItem.sort_order"
    )
    tags: Mapped[list["Tag"]] = relationship("Tag", secondary="practice_tags")


class PracticeLineItem(Base):
    """doc06 - sostituisce le ~30 colonne price_* fisse di V1 + Addendum H
    (subtype)."""

    __tablename__ = "practice_line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    practice_id: Mapped[int] = mapped_column(ForeignKey("practices.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    subtype: Mapped[str | None] = mapped_column(String(60))
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[PaymentChannel] = mapped_column(
        _pg_enum(PaymentChannel, "payment_channel"), nullable=False, default=PaymentChannel.W
    )
    urn_catalog_id: Mapped[int | None] = mapped_column(ForeignKey("urns.id"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
