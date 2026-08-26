"""Repository minimi per le tabelle di riferimento richieste dalle FK di
Pratica (company_locations, collaborators, urns, calendar_zones, tags) -
doc06 le classifica come 'tabelle che restano concettualmente invariate',
non domini con logica propria. Un file solo perche' ciascuna e' poche righe
di query identiche, non perche' condividano stato."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.calendar_zone import CalendarZone
from models.collaborator import Collaborator
from models.company_location import CompanyLocation
from models.tag import Tag
from models.urn import Urn


class CompanyLocationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, location_id: int) -> CompanyLocation | None:
        return await self._session.get(CompanyLocation, location_id)

    async def list_active(self) -> list[CompanyLocation]:
        stmt = select(CompanyLocation).where(CompanyLocation.active.is_(True)).order_by(CompanyLocation.name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_all(self) -> list[CompanyLocation]:
        """Vista di gestione (Admin): include anche le sedi disattivate,
        a differenza di list_active usata dai picker degli altri domini."""
        stmt = select(CompanyLocation).order_by(CompanyLocation.name)
        return list((await self._session.execute(stmt)).scalars().all())

    def add(self, location: CompanyLocation) -> None:
        self._session.add(location)


class CollaboratorRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, collaborator_id: int) -> Collaborator | None:
        return await self._session.get(Collaborator, collaborator_id)

    async def list_active(self) -> list[Collaborator]:
        stmt = select(Collaborator).where(Collaborator.active.is_(True)).order_by(Collaborator.name)
        return list((await self._session.execute(stmt)).scalars().all())


class UrnRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, urn_id: int) -> Urn | None:
        return await self._session.get(Urn, urn_id)

    async def list_active(self) -> list[Urn]:
        stmt = select(Urn).where(Urn.active.is_(True)).order_by(Urn.name)
        return list((await self._session.execute(stmt)).scalars().all())


class CalendarZoneRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, zone_id: int) -> CalendarZone | None:
        return await self._session.get(CalendarZone, zone_id)

    async def list_all(self) -> list[CalendarZone]:
        stmt = select(CalendarZone).order_by(CalendarZone.name)
        return list((await self._session.execute(stmt)).scalars().all())


class TagRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self) -> list[Tag]:
        stmt = select(Tag).order_by(Tag.category, Tag.label)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_by_ids(self, tag_ids: list[int]) -> list[Tag]:
        if not tag_ids:
            return []
        stmt = select(Tag).where(Tag.id.in_(tag_ids))
        return list((await self._session.execute(stmt)).scalars().all())
