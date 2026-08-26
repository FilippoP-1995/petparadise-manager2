from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.sessions import get_valid_session, touch_session
from database import get_session
from models.user import User, UserRole

SESSION_COOKIE_NAME = "ppm_v2_session"


async def get_current_user(
    db: AsyncSession = Depends(get_session),
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> User:
    """doc09 'Autenticazione': sessioni server-side con scadenza reale.
    Nessuna route protetta accede al DB utente senza passare da qui."""
    if session_token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessione mancante")
    session = await get_valid_session(db, session_token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessione non valida o scaduta")
    user = await db.get(User, session.user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utente non trovato o disattivato")
    await touch_session(db, session)
    return user


def require_role(*allowed_roles: UserRole):
    """doc09 'Permessi centralizzati': un'unica funzione, usata come
    Depends() su ogni route sensibile - elimina i controlli role!="admin"
    sparsi nel codice gia' rilevati nell'audit V1 (~20 punti, doc01)."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permesso negato")
        return user

    return _check
