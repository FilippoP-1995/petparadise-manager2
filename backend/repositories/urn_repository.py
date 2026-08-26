from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.urn import Urn, UrnCategory, UrnCodeCounter, UrnMovement

_CODE_PREFIX = {
    UrnCategory.urna: "URN",
    UrnCategory.accessorio: "ACC",
    UrnCategory.calco: "CALCO",
}


class UrnCatalogRepository:
    """Dominio Urne (Fase 5 punto 1, doc12) - distinta dalla
    reference_repositories.UrnRepository minimale (id+name, sola lettura,
    usata dai picker di Pratica)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, urn_id: int) -> Urn | None:
        return await self._session.get(Urn, urn_id)

    async def list_all(
        self,
        *,
        category: UrnCategory | None,
        active_only: bool,
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Urn]:
        stmt = select(Urn)
        if category is not None:
            stmt = stmt.where(Urn.category == category)
        if active_only:
            stmt = stmt.where(Urn.active.is_(True))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Urn.name.ilike(like), Urn.internal_code.ilike(like), Urn.material.ilike(like)))
        stmt = stmt.order_by(Urn.name).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().all())

    def add(self, urn: Urn) -> None:
        self._session.add(urn)

    async def next_internal_code(self, category: UrnCategory) -> str:
        """Stesso principio di PracticeRepository.next_practice_number:
        SELECT ... FOR UPDATE su un contatore dedicato per categoria,
        sostituisce la scansione 'primo codice libero' di V1 (race-prone
        sotto creazioni concorrenti)."""
        prefix = _CODE_PREFIX[category]
        stmt = select(UrnCodeCounter).where(UrnCodeCounter.key == category.value).with_for_update()
        counter = (await self._session.execute(stmt)).scalar_one_or_none()
        if counter is None:
            counter = UrnCodeCounter(key=category.value, next_value=1)
            self._session.add(counter)
            await self._session.flush()
        value = counter.next_value
        counter.next_value = value + 1
        return f"{prefix}-{value:03d}"


class UrnMovementRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    def add(self, movement: UrnMovement) -> None:
        self._session.add(movement)

    async def list_for_urn(self, urn_id: int, *, limit: int = 100) -> list[UrnMovement]:
        stmt = (
            select(UrnMovement)
            .where(UrnMovement.urn_id == urn_id)
            .order_by(UrnMovement.created_at.desc(), UrnMovement.id.desc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
