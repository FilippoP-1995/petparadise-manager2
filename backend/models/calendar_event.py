import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.base import TimestampMixin, pg_enum
from models.practice import PickupType


class CalendarEventType(str, enum.Enum):
    """doc06/doc14: solo Ritiro e Riconsegna sono costruiti in questa fase.

    DECISIONE TECNICA: V1 distingue anche 'Ritiro in sede'/'Riconsegna in
    sede' come valori separati di event_type (FACT verificato,
    calendar_service.py:20). In V2 quella distinzione e' gia' catturata in
    modo esplicito e non ambiguo da pickup_type='sede_aziendale' (Ritiro) e
    delivery_type='sede_aziendale' (Riconsegna, doc06 Addendum C/P) - la
    stessa logica "niente viene mai dedotto, ogni concetto e' un campo
    esplicito" gia' applicata ovunque in doc06 rende ridondante un secondo
    vocabolario per la stessa informazione. 'Appuntamento' (promemoria
    generico, non un Ritiro ne' una Riconsegna) resta esplicitamente FUORI
    SCOPE per questo dominio - non richiesto dai documenti ne' dal turno
    corrente, non aggiunto per non introdurre funzionalita' non richieste."""

    ritiro = "ritiro"
    riconsegna = "riconsegna"


class PickupStatus(str, enum.Enum):
    """doc14 §2 - enum chiuso, grafo di transizione in
    domain/pickup/state_machine.py. 'annullato' e' terminale (nessuna
    transizione in uscita, mai riapribile - decisione aziendale chiusa)."""

    da_confermare = "da_confermare"
    da_ritirare = "da_ritirare"
    ritirato = "ritirato"
    annullato = "annullato"


class DeliveryType(str, enum.Enum):
    """doc06 Addendum C (riscritto) - stesso principio del pickup_type
    sulla Pratica: nessuna logica di fallback, sempre una scelta esplicita."""

    ambulatorio = "ambulatorio"
    domicilio = "domicilio"
    sede_aziendale = "sede_aziendale"
    altro = "altro"


class CalendarEvent(Base, TimestampMixin):
    """Ritiro e Riconsegna: righe della stessa tabella, distinte da
    event_type (doc03 FACT, comportamento preservato). doc06 'Convenzioni
    generali - Soft delete': calendar_events e' esplicitamente una delle
    entita' con Cestino reale (deleted_at/deleted_by), a differenza delle
    tabelle di dettaglio."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        # Release hardening (gate 6): dimostrato con EXPLAIN ANALYZE su un
        # dataset sintetico di 50k righe (~65x piu' veloce sulla vista
        # giorno del Calendario, non aggiunto solo perche' "mancava") -
        # serve sia la vista giorno del Calendario (event_type +
        # intervallo start_at) sia le liste Ritiri/Riconsegne esistenti
        # (event_type, ordinate per start_at).
        Index(
            "ix_calendar_events_active_type_start",
            "event_type",
            text("start_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[CalendarEventType] = mapped_column(pg_enum(CalendarEventType, "calendar_event_type"), nullable=False)

    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    # Ruolo "punto di affido" per pickup_type='veterinario' (doc06 Addendum
    # C: "nessun campo aggiuntivo lo duplica") - stesso nome colonna gia'
    # in uso in V1 (FACT, calendar_service.py:41), riusato as-is.
    veterinarian_id: Mapped[int | None] = mapped_column(ForeignKey("veterinarians.id", ondelete="SET NULL"))
    # Ruolo "punto di affido" per pickup_type='collaboratore' - stesso
    # principio di veterinarian_id sopra, colonna nuova (V1 non aveva
    # collaboratori come punto di affido di un Ritiro).
    collaborator_id: Mapped[int | None] = mapped_column(ForeignKey("collaborators.id", ondelete="SET NULL"))

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    # Relazione bidirezionale con practices.originating_pickup_event_id
    # (doc06 'Relazione Ritiro -> Pratica'). SET NULL: un ritiro cancellato
    # non deve mai far sparire ne' bloccare la pratica gia' generata.
    linked_practice_id: Mapped[int | None] = mapped_column(ForeignKey("practices.id", ondelete="SET NULL"))

    # --- Ritiro (doc14 §2 + doc06 Addendum C) ---
    pickup_status: Mapped[PickupStatus | None] = mapped_column(pg_enum(PickupStatus, "pickup_status"))
    # Riusa il TYPE Postgres 'pickup_type' gia' creato dalla migrazione
    # practices (create_type=False: non ricrearlo).
    pickup_type: Mapped[PickupType | None] = mapped_column(pg_enum(PickupType, "pickup_type", create_type=False))
    pickup_location_id: Mapped[int | None] = mapped_column(ForeignKey("company_locations.id"))
    pickup_zone_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_zones.id"))
    pickup_address: Mapped[str | None] = mapped_column(Text)
    pickup_contact_name: Mapped[str | None] = mapped_column(Text)

    # --- Riconsegna (doc06 Addendum P - nessuna macchina a stati, doc14 §3) ---
    delivery_type: Mapped[DeliveryType | None] = mapped_column(pg_enum(DeliveryType, "delivery_type"))
    delivery_veterinarian_id: Mapped[int | None] = mapped_column(ForeignKey("veterinarians.id", ondelete="SET NULL"))
    delivery_location_id: Mapped[int | None] = mapped_column(ForeignKey("company_locations.id"))
    delivery_zone_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_zones.id"))
    delivery_address: Mapped[str | None] = mapped_column(Text)
    # doc06 Addendum P: "preliminare", MAI letto quando linked_practice_id
    # e' valorizzato - congelamento enforcement nel service layer, non qui.
    preliminary_payment_status: Mapped[str | None] = mapped_column(Text)
    preliminary_payment_amount: Mapped[int | None] = mapped_column(BigInteger)

    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animals: Mapped[list["Animal"]] = relationship(
        "Animal", cascade="all, delete-orphan", order_by="Animal.sort_order"
    )
