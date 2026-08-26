from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.client import Client


class ClientRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, client_id: int) -> Client | None:
        return await self._session.get(Client, client_id)

    async def list_active(self, *, search: str | None, limit: int, offset: int) -> list[Client]:
        stmt = select(Client).where(Client.active.is_(True))
        if search:
            pattern = f"%{search.lower()}%"
            stmt = stmt.where(
                (Client.first_name.ilike(pattern))
                | (Client.last_name.ilike(pattern))
                | (Client.company_name.ilike(pattern))
                | (Client.phone.ilike(pattern))
                | (Client.email.ilike(pattern))
            )
        stmt = stmt.order_by(Client.last_name, Client.first_name).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    def add(self, client: Client) -> None:
        self._session.add(client)
