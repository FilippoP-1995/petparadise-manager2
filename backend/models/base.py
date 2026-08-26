from datetime import datetime

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.orm import Mapped, mapped_column


def pg_enum(enum_cls: type, name: str, *, create_type: bool = True) -> Enum:
    """SQLAlchemy usa di default il NOME del membro Python (non il suo
    .value) come label dell'ENUM Postgres - irrilevante quando nome e
    valore coincidono, ma es. PaymentChannel.collaboratori ha
    value='Collaboratori': senza values_callable il DB memorizzerebbe
    'collaboratori', diverso dal letterale 'Collaboratori' di doc06.

    create_type=False quando lo stesso ENUM Postgres e' gia' stato creato
    da una migrazione precedente per un'altra tabella (es. pickup_type gia'
    creato per practices) - evita un CREATE TYPE duplicato."""
    return Enum(enum_cls, name=name, values_callable=lambda cls: [e.value for e in cls], create_type=create_type)


class TimestampMixin:
    """doc06 'Convenzioni generali': created_at/updated_at su ogni tabella,
    valorizzati dal database (server_default/onupdate), mai scritti a mano
    dal codice applicativo come in V1."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
