from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.veterinarian import Veterinarian


class VeterinarianRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, veterinarian_id: int) -> Veterinarian | None:
        stmt = (
            select(Veterinarian)
            .where(Veterinarian.id == veterinarian_id)
            .options(selectinload(Veterinarian.hours))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, *, search: str | None, limit: int, offset: int) -> list[Veterinarian]:
        stmt = select(Veterinarian).where(Veterinarian.active.is_(True)).options(selectinload(Veterinarian.hours))
        if search:
            pattern = f"%{search.lower()}%"
            stmt = stmt.where(
                (Veterinarian.clinic_name.ilike(pattern))
                | (Veterinarian.doctor_name.ilike(pattern))
                | (Veterinarian.city.ilike(pattern))
            )
        stmt = stmt.order_by(Veterinarian.clinic_name).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().unique().all())

    def add(self, veterinarian: Veterinarian) -> None:
        self._session.add(veterinarian)
