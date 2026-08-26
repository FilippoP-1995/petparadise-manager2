from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Animal(Base):
    """doc06 (versione finale, sezione 'calendar_events + figlie'): tabella
    condivisa Ritiro/Pratica, N animali senza limite artificiale (elimina
    animal2_* di V1). Un animale nasce collegato solo a calendar_event_id
    (Ritiro non ancora diventato pratica); quando il Ritiro diventa pratica,
    la STESSA riga riceve anche practice_id (mai una copia nuova, mai
    ricreata) - questo e' l'esatto meccanismo che risolve il bug V1 dove
    solo il primo animale di un Ritiro multi-animale sopravviveva alla
    conversione in pratica (verificato: app.py:15733,15740)."""

    __tablename__ = "animals"
    __table_args__ = (
        CheckConstraint("calendar_event_id IS NOT NULL OR practice_id IS NOT NULL", name="ck_animals_has_owner"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_event_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_events.id", ondelete="CASCADE"))
    practice_id: Mapped[int | None] = mapped_column(ForeignKey("practices.id", ondelete="CASCADE"))
    # Gate Animali<->Cicli (round 2, confermato dall'utente): fonte di
    # verita' dell'assegnazione al ciclo di cremazione, a livello ANIMALE
    # (non piu' practices.cremation_cycle_id, rimosso). ON DELETE SET NULL
    # - relazione debole per design (doc05): eliminare un ciclo non
    # completato lascia semplicemente l'animale non assegnato, mai
    # cancellato. Un ciclo 'completato' non e' comunque mai eliminabile
    # (vietato per costruzione, doc14 SS4) quindi questo SET NULL scatta
    # solo per cicli pianificato/in_attesa.
    cremation_cycle_id: Mapped[int | None] = mapped_column(ForeignKey("cremation_cycles.id", ondelete="SET NULL"))
    name: Mapped[str | None] = mapped_column(String(120))
    species: Mapped[str | None] = mapped_column(String(60))
    breed: Mapped[str | None] = mapped_column(String(120))
    age_years: Mapped[int | None] = mapped_column(SmallInteger)
    age_months: Mapped[int | None] = mapped_column(SmallInteger)
    estimated_weight_grams: Mapped[int | None] = mapped_column(Integer)
    microchip: Mapped[str | None] = mapped_column(String(60))
    cremation_type: Mapped[str | None] = mapped_column(String(60))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
