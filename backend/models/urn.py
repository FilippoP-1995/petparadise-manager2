import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import TimestampMixin, pg_enum


class UrnCategory(str, enum.Enum):
    """FACT V1 (urn_catalog_page/urn_edit_page): un solo catalogo, tre
    categorie condivise (tab Urne/Accessori/Calchi), non tre tabelle
    separate - preservato identico in V2."""

    urna = "Urna"
    accessorio = "Accessorio"
    calco = "Calco"


class Urn(TimestampMixin, Base):
    """doc06 'tabelle che restano concettualmente invariate' + Addendum L
    (urn_movements). Dominio Sedi/Urne/Articoli (Fase 5 punto 1, doc12):
    schema completo portato da V1 (categoria/materiale/codice
    interno/prezzo/quantita/soglia scorte/note), nessuna incoerenza
    trovata - solo pulizia tipi (prezzo in centesimi, come da convenzione
    'Denaro' di doc06). Upload immagine deliberatamente ESCLUSO in questo
    passaggio: nessuna strategia di storage file/media ancora decisa per
    V2 (verra' progettata centralmente insieme a DDT/altri media)."""

    __tablename__ = "urns"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[UrnCategory] = mapped_column(
        pg_enum(UrnCategory, "urn_category"), nullable=False, default=UrnCategory.urna
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    material: Mapped[str | None] = mapped_column(String(100))
    internal_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class UrnCodeCounter(Base):
    """Stesso principio di PracticeNumberCounter (models/practice.py):
    sostituisce la scansione 'trova il primo codice libero' di V1
    (save_urn, prefisso per categoria URN-/ACC-/CALCO-) con un contatore
    dedicato e SELECT ... FOR UPDATE - stessa logica di business, senza la
    race condition possibile sotto creazioni concorrenti."""

    __tablename__ = "urn_code_counters"

    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    next_value: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class UrnMovement(Base):
    """doc06 Addendum L: tabella preservata identica da V1, stesso ruolo
    (log movimenti di magazzino) - append-only, solo created_at (nessun
    updated_at: una riga di movimento non si modifica mai dopo la
    creazione, stesso principio gia' applicato ad audit_log)."""

    __tablename__ = "urn_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    urn_id: Mapped[int] = mapped_column(ForeignKey("urns.id"), nullable=False)
    practice_id: Mapped[int | None] = mapped_column(ForeignKey("practices.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    movement_type: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    old_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    new_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
