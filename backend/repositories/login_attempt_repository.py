from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.login_attempt import LoginAttempt


class LoginAttemptRepository:
    """Release hardening: rate limit temporaneo su /api/auth/login, per
    username_key (non per IP, non un contatore globale) - vedi il
    docstring di LoginAttempt per il ragionamento completo."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def count_recent_failures(self, username_key: str, *, since: datetime) -> int:
        stmt = select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.username_key == username_key, LoginAttempt.created_at >= since
        )
        return (await self._session.execute(stmt)).scalar_one()

    def record_failure(self, username_key: str) -> None:
        self._session.add(LoginAttempt(username_key=username_key))

    async def clear(self, username_key: str) -> None:
        await self._session.execute(delete(LoginAttempt).where(LoginAttempt.username_key == username_key))
