from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class LoginAttempt(Base):
    """Release hardening: rate limit temporaneo su /api/auth/login. Solo i
    tentativi FALLITI vengono registrati (append-only, come audit_log) -
    un login riuscito cancella le righe della propria chiave invece di
    aggiungerne una, cosi' il contatore si azzera immediatamente senza
    bisogno di un JOIN sull'ultimo successo. Chiave = username normalizzato
    cosi' come inviato (esiste o no come utente reale): stesso trattamento
    per non rivelare, tramite il comportamento del rate limit stesso, se un
    nome utente esiste o meno."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username_key: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
