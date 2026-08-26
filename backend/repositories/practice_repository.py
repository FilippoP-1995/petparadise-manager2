from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.client import Client
from models.practice import Practice, PracticeNumberCounter

_EAGER_OPTIONS = (
    selectinload(Practice.animals),
    selectinload(Practice.line_items),
    selectinload(Practice.tags),
)

# doc06 'Relazione Ritiro -> Pratica' + app.py:1323-1330 (FACT, prefissi
# gia' in uso in V1): stessa logica esatta, tabella dedicata invece del
# generico settings.
_PREFIX_BY_KEY = {
    "next_collab_number": "COL-",
    "next_cr_number": "CR-",
    "next_sm_number": "SM-",
    "next_practice_number": "PP-",
}


def practice_number_key(service_type: str, request_origin: str) -> str:
    if request_origin == "Collaboratore":
        return "next_collab_number"
    if service_type == "Cremazione singola":
        return "next_cr_number"
    if service_type == "Cremazione collettiva":
        return "next_sm_number"
    return "next_practice_number"


class PracticeRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, practice_id: int, *, include_deleted: bool = False) -> Practice | None:
        stmt = select(Practice).where(Practice.id == practice_id).options(*_EAGER_OPTIONS)
        practice = (await self._session.execute(stmt)).scalar_one_or_none()
        if practice is not None and practice.deleted_at is not None and not include_deleted:
            return None
        return practice

    async def list_active(
        self, *, search: str | None, status: str | None, limit: int, offset: int
    ) -> list[Practice]:
        stmt = (
            select(Practice)
            .join(Client, Client.id == Practice.client_id)
            .where(Practice.deleted_at.is_(None))
            .options(*_EAGER_OPTIONS)
        )
        if search:
            pattern = f"%{search.lower()}%"
            stmt = stmt.where(
                or_(
                    Practice.practice_number.ilike(pattern),
                    Client.first_name.ilike(pattern),
                    Client.last_name.ilike(pattern),
                    Client.company_name.ilike(pattern),
                )
            )
        if status:
            stmt = stmt.where(Practice.status == status)
        stmt = stmt.order_by(Practice.created_at.desc()).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().unique().all())

    def add(self, practice: Practice) -> None:
        self._session.add(practice)

    async def next_practice_number(self, *, service_type: str, request_origin: str) -> str:
        """doc06: stesso schema di numerazione gia' in uso e verificato in
        V1 (contatore sequenziale per prefisso), migrato in una tabella
        dedicata. SELECT ... FOR UPDATE per evitare un duplicato sotto
        creazioni concorrenti (Postgres reale lo permette, SQLite di V1 no -
        miglioria tecnica diretta del passaggio di database, non una
        modifica alla regola di business)."""
        key = practice_number_key(service_type, request_origin)
        prefix = _PREFIX_BY_KEY[key]
        stmt = select(PracticeNumberCounter).where(PracticeNumberCounter.key == key).with_for_update()
        counter = (await self._session.execute(stmt)).scalar_one_or_none()
        if counter is None:
            counter = PracticeNumberCounter(key=key, next_value=1)
            self._session.add(counter)
            await self._session.flush()
        value = counter.next_value
        counter.next_value = value + 1
        return f"{prefix}{value:06d}"
