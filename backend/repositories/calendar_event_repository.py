from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from models.calendar_event import CalendarEvent, CalendarEventType
from models.client import Client

_EAGER_OPTIONS = (selectinload(CalendarEvent.animals),)


class CalendarEventRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, event_id: int, *, include_deleted: bool = False) -> CalendarEvent | None:
        stmt = select(CalendarEvent).where(CalendarEvent.id == event_id).options(*_EAGER_OPTIONS)
        event = (await self._session.execute(stmt)).scalar_one_or_none()
        if event is not None and event.deleted_at is not None and not include_deleted:
            return None
        return event

    async def get_by_id_for_update(self, event_id: int) -> CalendarEvent | None:
        """Blocca la riga (SELECT ... FOR UPDATE) per la durata della
        transazione - richiesto per Ritiro -> Pratica: senza questo lock,
        due richieste concorrenti potrebbero superare entrambe il controllo
        'non ancora collegato' e generare due pratiche duplicate (bug reale
        gia' noto in V1, dove solo il doppio-collegamento era prevenuto, non
        la doppia creazione - vedi report di dominio)."""
        stmt = select(CalendarEvent).where(CalendarEvent.id == event_id).options(*_EAGER_OPTIONS).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_active(
        self,
        *,
        event_type: CalendarEventType | None,
        search: str | None,
        pickup_status: str | None,
        limit: int,
        offset: int,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[CalendarEvent]:
        stmt = select(CalendarEvent).where(CalendarEvent.deleted_at.is_(None)).options(*_EAGER_OPTIONS)
        if event_type is not None:
            stmt = stmt.where(CalendarEvent.event_type == event_type)
        if pickup_status:
            stmt = stmt.where(CalendarEvent.pickup_status == pickup_status)
        if date_from is not None:
            stmt = stmt.where(CalendarEvent.start_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(CalendarEvent.start_at < date_to)
        if search:
            pattern = f"%{search.lower()}%"
            stmt = stmt.outerjoin(Client, Client.id == CalendarEvent.client_id).where(
                or_(
                    Client.first_name.ilike(pattern),
                    Client.last_name.ilike(pattern),
                    Client.company_name.ilike(pattern),
                    CalendarEvent.notes.ilike(pattern),
                )
            )
        # Tiebreaker su id: senza una seconda chiave di ordinamento, due
        # eventi con lo stesso start_at non hanno un ordine garantito da
        # SQL (potrebbe cambiare tra due query identiche) - rilevante per
        # il Calendario, dove piu' eventi nello stesso slot orario sono un
        # caso reale, non ipotetico.
        stmt = stmt.order_by(CalendarEvent.start_at.desc(), CalendarEvent.id.desc()).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().unique().all())

    def add(self, event: CalendarEvent) -> None:
        self._session.add(event)
