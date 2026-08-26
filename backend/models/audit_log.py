from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AuditLog(Base):
    """doc06 'Audit trail (storico modifiche) - unificato': un'unica tabella
    per tutto il progetto, non N tabelle di storico parallele come in V1.
    Solo created_at (mai updated_at): una riga di audit e' append-only per
    natura, non ha senso una data di "ultima modifica"."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    # doc14 (correzioni eccezionali di stato): il motivo e' obbligatorio per
    # una correzione, ma il modello generico doc06 non prevedeva una colonna
    # dedicata - aggiunta additiva, NULL per ogni azione che non lo richiede.
    reason: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
