from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import TimestampMixin


class Collaborator(TimestampMixin, Base):
    """doc06 'tabelle che restano concettualmente invariate': tabella
    minima sufficiente a rendere reale la FK practices.collaborator_id -
    non un dominio con macchina a stati propria."""

    __tablename__ = "collaborators"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
