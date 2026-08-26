"""Crea l'utente admin di sviluppo, se non esiste gia'. Solo per ambiente
di sviluppo/test - non un meccanismo di produzione (la gestione utenti
reale e' un dominio V2 futuro, non ancora implementato)."""

import asyncio
import sys

from sqlalchemy import select

from auth.security import hash_password
from database import async_session_factory
from models.user import User, UserRole


async def main() -> None:
    async with async_session_factory() as session:
        existing = await session.execute(select(User).where(User.username == "admin"))
        if existing.scalar_one_or_none() is not None:
            print("admin gia' presente")
            return
        user = User(
            username="admin",
            password_hash=hash_password("dev-password-change-me"),
            display_name="Amministratore",
            role=UserRole.admin,
            active=True,
            must_change_password=True,
        )
        session.add(user)
        await session.commit()
        print(f"creato utente admin, id={user.id}")


if __name__ == "__main__":
    asyncio.run(main())
