from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class Tag(Base):
    """doc06 '5. Un secondo animale e i tag come colonne fisse': vocabolario
    estendibile senza migrazione, sostituisce le 14 colonne tag_* di V1."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str | None] = mapped_column(String(60))


class PracticeTag(Base):
    __tablename__ = "practice_tags"

    practice_id: Mapped[int] = mapped_column(ForeignKey("practices.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)
