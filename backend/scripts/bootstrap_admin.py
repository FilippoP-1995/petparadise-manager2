"""Production bootstrap / one-time admin creation.

Crea il primissimo utente amministratore V2 in produzione - a differenza
di seed_dev_admin.py (SOLO sviluppo/test, credenziali hardcoded e note),
qui username e password arrivano esclusivamente da variabili d'ambiente:
nessuna password e' scritta nel repository ne' stampata in output/log. Se
un utente con quello username esiste gia', lo script non lo tocca (nessun
sovrascrizione automatica della password) - va eseguito una sola volta,
manualmente, contro il database V2, dopo `alembic upgrade head` e prima
del primo login reale.

Uso (da backend/, con backend/ come working directory - richiesto perche'
gli import interni non sono qualificati come backend.xxx):
    PPM_V2_ADMIN_USERNAME=... PPM_V2_ADMIN_PASSWORD=... \
        python -m scripts.bootstrap_admin

Variabili d'ambiente:
    PPM_V2_ADMIN_USERNAME       obbligatoria
    PPM_V2_ADMIN_PASSWORD       obbligatoria
    PPM_V2_ADMIN_DISPLAY_NAME   opzionale (default: "Amministratore")
"""

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.security import hash_password
from database import async_session_factory
from models.user import User, UserRole


async def bootstrap_admin(db: AsyncSession, *, username: str, password: str, display_name: str) -> User | None:
    """Crea l'utente admin se non esiste gia'. Ritorna il nuovo utente, o
    None se un utente con quello username era gia' presente (in quel caso
    nessuna modifica viene effettuata - la password esistente non viene
    mai sovrascritta automaticamente)."""
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none() is not None:
        return None

    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        role=UserRole.admin,
        active=True,
        # Non "must_change_password=True": a differenza della password di
        # sviluppo nota nel codice (seed_dev_admin.py), questa e' gia' una
        # password scelta dall'operatore e passata via env - non c'e' un
        # valore condiviso/noto da forzare a cambiare.
        must_change_password=False,
    )
    db.add(user)
    await db.flush()
    return user


async def _run_from_cli() -> int:
    username = os.environ.get("PPM_V2_ADMIN_USERNAME", "").strip()
    password = os.environ.get("PPM_V2_ADMIN_PASSWORD", "")
    display_name = os.environ.get("PPM_V2_ADMIN_DISPLAY_NAME", "Amministratore").strip()

    if not username or not password:
        print(
            "Errore: impostare PPM_V2_ADMIN_USERNAME e PPM_V2_ADMIN_PASSWORD "
            "nell'ambiente prima di eseguire questo script.",
            file=sys.stderr,
        )
        return 1

    async with async_session_factory() as session:
        user = await bootstrap_admin(session, username=username, password=password, display_name=display_name)
        if user is None:
            print(f"Utente '{username}' gia' presente: nessuna modifica effettuata.")
            return 0
        await session.commit()
        print(f"Creato utente admin '{username}', id={user.id}.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_from_cli()))
