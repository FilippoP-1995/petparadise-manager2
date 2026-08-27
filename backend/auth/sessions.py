import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.session import Session

SESSION_TOKEN_BYTES = 32


async def create_session(db: AsyncSession, *, user_id: int, ip: str | None, user_agent: str | None) -> Session:
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    session = Session(
        id=token,
        user_id=user_id,
        expires_at=now + timedelta(days=settings.session_ttl_days),
        ip=ip,
        user_agent=user_agent,
    )
    db.add(session)
    return session


async def get_valid_session(db: AsyncSession, token: str) -> Session | None:
    session = await db.get(Session, token)
    if session is None:
        return None
    if session.expires_at < datetime.now(timezone.utc):
        return None
    return session


async def touch_session(db: AsyncSession, session: Session) -> None:
    """Rinnova last_seen_at ad ogni richiesta autenticata (doc09: TTL
    scorrevole basato sull'ultima attivita', non sulla creazione)."""
    session.last_seen_at = datetime.now(timezone.utc)


async def delete_session(db: AsyncSession, token: str) -> None:
    """Release hardening: invalidazione server-side al logout - senza,
    una sessione rimane valida fino alla scadenza naturale anche dopo che
    l'utente ha 'fatto logout' (solo il cookie veniva cancellato). Un
    token gia' assente/scaduto e' un no-op sicuro (get non solleva)."""
    session = await db.get(Session, token)
    if session is not None:
        await db.delete(session)
        await db.commit()
