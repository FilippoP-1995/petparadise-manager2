from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import TimestampMixin


class Client(TimestampMixin, Base):
    """doc06: 'clients' resta concettualmente invariata rispetto a V1 (nessuna
    incoerenza rilevata dall'audit) - qui con FK/timestamp standardizzati."""

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    company_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    phone_2: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(200))
    tax_code: Mapped[str | None] = mapped_column(String(32))
    vat_number: Mapped[str | None] = mapped_column(String(32))
    street: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(120))
    province: Mapped[str | None] = mapped_column(String(8))
    zip: Mapped[str | None] = mapped_column(String(16))
    address: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
