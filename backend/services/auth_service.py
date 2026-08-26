from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.security import verify_password
from auth.sessions import create_session
from domain.errors import ValidationDomainError
from models.session import Session
from models.user import User


async def login(db: AsyncSession, *, username: str, password: str, ip: str | None, user_agent: str | None) -> Session:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    # Stesso messaggio generico sia per utente inesistente sia per password
    # errata (mai rivelare quale dei due e' sbagliato).
    if user is None or not user.active or not verify_password(password, user.password_hash):
        raise ValidationDomainError("Credenziali non valide")

    session = await create_session(db, user_id=user.id, ip=ip, user_agent=user_agent)
    await db.commit()
    return session
