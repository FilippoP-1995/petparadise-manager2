from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import TimestampMixin


class CompanyLocation(TimestampMixin, Base):
    """doc06 'tabelle che restano concettualmente invariate' + Addendum C:
    sedi aziendali reali (oggi Livorno/Empoli), FK reale sostituisce il
    TEXT libero che practices.destination_branch aveva in V1."""

    __tablename__ = "company_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    has_cremation_plant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
