from sqlalchemy import Boolean, ForeignKey, Numeric, SmallInteger, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base
from models.base import TimestampMixin


class Veterinarian(TimestampMixin, Base):
    """doc06: 'veterinarians' (+veterinarian_hours) resta concettualmente
    invariata rispetto a V1."""

    __tablename__ = "veterinarians"

    id: Mapped[int] = mapped_column(primary_key=True)
    clinic_name: Mapped[str | None] = mapped_column(String(200))
    doctor_name: Mapped[str | None] = mapped_column(String(200))
    short_name: Mapped[str | None] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    google_place_id: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lng: Mapped[float | None] = mapped_column(Numeric(9, 6))
    service_duration_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    hours: Mapped[list["VeterinarianHours"]] = relationship(
        back_populates="veterinarian", cascade="all, delete-orphan", order_by="VeterinarianHours.day_of_week"
    )


class VeterinarianHours(Base):
    """Riga per giorno della settimana - tabella figlia di dettaglio, hard
    delete reale con ON DELETE CASCADE (doc06 'Soft delete': niente cestino
    per una singola riga di dettaglio)."""

    __tablename__ = "veterinarian_hours"

    id: Mapped[int] = mapped_column(primary_key=True)
    veterinarian_id: Mapped[int] = mapped_column(ForeignKey("veterinarians.id", ondelete="CASCADE"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=lunedi .. 6=domenica
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    morning_start: Mapped[str | None] = mapped_column(Time)
    morning_end: Mapped[str | None] = mapped_column(Time)
    afternoon_start: Mapped[str | None] = mapped_column(Time)
    afternoon_end: Mapped[str | None] = mapped_column(Time)
    notes: Mapped[str | None] = mapped_column(String(255))

    veterinarian: Mapped["Veterinarian"] = relationship(back_populates="hours")
