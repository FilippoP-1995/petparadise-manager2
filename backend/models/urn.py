from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import TimestampMixin


class Urn(TimestampMixin, Base):
    """doc06 'tabelle che restano concettualmente invariate' + Addendum L
    (urn_movements): tabella minima sufficiente a rendere reale la FK
    practice_line_items.urn_catalog_id e a tracciare la scorta corrente.
    Il ledger dei movimenti (urn_movements) e' fuori scope per il dominio
    Pratiche - appartiene al dominio Magazzino/Articoli, non ancora
    costruito in V2."""

    __tablename__ = "urns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
