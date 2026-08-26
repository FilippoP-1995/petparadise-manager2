from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.animal import Animal
from models.cremation_cycle import CremationCycle

_EAGER_OPTIONS = (selectinload(CremationCycle.animals),)


class CremationCycleRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, cycle_id: int) -> CremationCycle | None:
        stmt = select(CremationCycle).where(CremationCycle.id == cycle_id).options(*_EAGER_OPTIONS)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_for_update(self, cycle_id: int) -> CremationCycle | None:
        """Lock di riga per tutta la transazione - stesso principio gia'
        usato e testato per Ritiro->Pratica: previene che due assegnazioni
        concorrenti superino entrambe il controllo di capacita'."""
        stmt = select(CremationCycle).where(CremationCycle.id == cycle_id).options(*_EAGER_OPTIONS).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_active(self, *, status: str | None, cycle_date: str | None, limit: int, offset: int) -> list[CremationCycle]:
        stmt = select(CremationCycle).options(*_EAGER_OPTIONS)
        if status:
            stmt = stmt.where(CremationCycle.status == status)
        if cycle_date:
            stmt = stmt.where(CremationCycle.cycle_date == cycle_date)
        stmt = stmt.order_by(CremationCycle.cycle_date.desc(), CremationCycle.sort_order).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().unique().all())

    def add(self, cycle: CremationCycle) -> None:
        self._session.add(cycle)


class AnimalCycleRepository:
    """Accesso ad Animal per lo scopo specifico dell'assegnazione al ciclo
    - non un secondo repository generico per Animal (che non esiste come
    entita' autonoma con le proprie route/API in questa fase, resta
    gestito tramite Pratica/Ritiro)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_for_update(self, animal_id: int) -> Animal | None:
        stmt = select(Animal).where(Animal.id == animal_id).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_unassigned_eligible(self, *, limit: int) -> list[Animal]:
        """doc03 FACT (coda animali in attesa di ciclo, tradotta a
        granularita' animale): pratiche 'Cremazione singola' non ancora
        consegnate/smaltite, animali non gia' assegnati a un ciclo."""
        from models.practice import Practice, PracticeStatus

        stmt = (
            select(Animal)
            .join(Practice, Practice.id == Animal.practice_id)
            .where(
                Animal.cremation_cycle_id.is_(None),
                Practice.service_type == "Cremazione singola",
                Practice.status.notin_([PracticeStatus.consegnato, PracticeStatus.smaltito]),
                Practice.deleted_at.is_(None),
            )
            .order_by(Animal.id)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
