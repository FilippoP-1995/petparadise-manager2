import enum
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, ForeignKey, SmallInteger, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.base import TimestampMixin, pg_enum


class CremationCycleStatus(str, enum.Enum):
    """doc14 §4 - invariati da V1 (CHECK gia' presente a livello DB in V1),
    grafo di transizione in domain/cremation_cycle/state_machine.py."""

    pianificato = "pianificato"
    in_attesa = "in_attesa"
    completato = "completato"


class CremationCycle(TimestampMixin, Base):
    """doc06 Addendum C (cremation_location_id) + doc14 §4. FACT V1
    (app.py:351-361): cycle_date/planned_start/planned_end/sort_order sono
    colonne reali attivamente usate per la programmazione; actual_start e'
    un campo orfano mai scritto (doc03 FACT, non portato avanti);
    actual_end e' invece attivamente scritto/letto ('Completato alle
    HH:MM') - portato avanti come completed_at, tipizzato."""

    __tablename__ = "cremation_cycles"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[CremationCycleStatus] = mapped_column(
        pg_enum(CremationCycleStatus, "cremation_cycle_status"),
        nullable=False,
        default=CremationCycleStatus.pianificato,
    )
    planned_start: Mapped[time] = mapped_column(Time, nullable=False)
    planned_end: Mapped[time] = mapped_column(Time, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # doc06 Addendum C: "vive sul ciclo di cremazione... sede fisica dove
    # avviene l'operazione condivisa da tutti gli animali assegnati a quel
    # ciclo" - deliberatamente separata da practices.destination_branch_id.
    cremation_location_id: Mapped[int | None] = mapped_column(ForeignKey("company_locations.id"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    animals: Mapped[list["Animal"]] = relationship(
        "Animal",
        # NIENTE cascade='delete-orphan' qui (a differenza di
        # Practice.animals/CalendarEvent.animals): un animale rimosso da
        # un ciclo resta comunque posseduto dalla propria Pratica - non va
        # mai cancellato per il solo fatto di essere stato riassegnato o
        # rimosso da un ciclo. Relazione debole per design (doc05).
        order_by="Animal.sort_order",
    )
