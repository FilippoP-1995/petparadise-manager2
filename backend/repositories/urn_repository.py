from sqlalchemy import case, func, or_, select
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

    async def category_stats(self, category: UrnCategory) -> dict[str, int]:
        """V1 (urn_catalog_page: stats={'models':...,'quantity':...,'out':...,
        'low':...,'value':...}) - stessa esatta formula, qui una sola query
        aggregata sugli articoli attivi della categoria invece del loop
        Python su all_urns: 'out' = scorta a 0 o negativa, 'low' = scorta
        positiva ma sotto/pari alla soglia, mai sovrapposti tra loro."""
        clamped_quantity = func.greatest(Urn.quantity, 0)
        stmt = select(
            func.count().label("models"),
            func.coalesce(func.sum(clamped_quantity), 0).label("quantity"),
            func.coalesce(func.sum(case((Urn.quantity <= 0, 1), else_=0)), 0).label("out"),
            func.coalesce(func.sum(case(((Urn.quantity > 0) & (Urn.quantity <= Urn.low_stock_threshold), 1), else_=0)), 0).label(
                "low"
            ),
            func.coalesce(func.sum(clamped_quantity * Urn.price_cents), 0).label("value_cents"),
        ).where(Urn.category == category, Urn.active.is_(True))
        row = (await self._session.execute(stmt)).one()
        return {"models": row.models, "quantity": row.quantity, "out": row.out, "low": row.low, "value_cents": row.value_cents}

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
