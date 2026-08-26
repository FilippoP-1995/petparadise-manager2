from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import TimestampMixin


class Article(TimestampMixin, Base):
    """doc06 'tabelle che restano concettualmente invariate'. FACT V1
    (app.py:661-662, articles_page): un catalogo statico di prodotti
    richiedibili ('Da ordinare') - in V1 non esiste nessuna pagina di
    gestione/CRUD per questa tabella, solo un seed fisso di 6 nomi
    (preservato in migrazione, stesso principio gia' usato per i 14 tag
    fissi). Distinto e FUORI SCOPE da questo dominio: `email_orders`/
    `order_settings_page`, il sistema di ordini fornitori via email molto
    piu' grande, mai nominato nella roadmap (doc12 Fase 5 punto 1)."""

    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ArticleOrder(Base):
    """FACT V1 (order_article): registra solo la richiesta interna
    ('Ordina prodotto') - nessuna email, nessun fornitore. Append-only,
    stesso principio di UrnMovement/AuditLog (solo created_at)."""

    __tablename__ = "article_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False)
    ordered_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
