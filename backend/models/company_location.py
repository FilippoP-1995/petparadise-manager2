from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base import TimestampMixin


class CompanyLocation(TimestampMixin, Base):
    """doc06 'tabelle che restano concettualmente invariate' + Addendum C:
    sedi aziendali reali (oggi Livorno/Empoli), FK reale sostituisce il
    TEXT libero che practices.destination_branch aveva in V1.

    Dominio Sedi/Urne/Articoli (Fase 5 punto 1, doc12): CRUD Admin-only,
    stesso perimetro di V1 (route_locations_page/save_route_location,
    'Solo gli amministratori possono modificare le sedi aziendali' - FACT,
    403 reale gia' in V1). Deliberatamente SENZA address/lat/lng/geocodifica:
    in V1 quei campi esistono solo per il Percorso Giornaliero, un dominio
    non ancora costruito in V2 - non anticipato qui."""

    __tablename__ = "company_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    has_cremation_plant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
