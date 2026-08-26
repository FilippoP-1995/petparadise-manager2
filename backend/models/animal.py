from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Animal(Base):
    """doc06 (versione finale, sezione 'calendar_events + figlie'): tabella
    condivisa Ritiro/Pratica, N animali senza limite artificiale (elimina
    animal2_* di V1). calendar_event_id resta SENZA vincolo FK per ora: il
    dominio Ritiro (calendar_events) non e' ancora stato costruito in V2 -
    la colonna esiste per compatibilita' futura (stessa riga, mai copiata,
    quando un Ritiro diventa Pratica), il vincolo FK verra' aggiunto con
    una migrazione dedicata quando quel dominio verra' costruito."""

    __tablename__ = "animals"
    __table_args__ = (
        CheckConstraint("calendar_event_id IS NOT NULL OR practice_id IS NOT NULL", name="ck_animals_has_owner"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_event_id: Mapped[int | None] = mapped_column(Integer)  # FK futura, vedi docstring
    practice_id: Mapped[int | None] = mapped_column(ForeignKey("practices.id", ondelete="CASCADE"))
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
