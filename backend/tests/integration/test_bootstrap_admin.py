from sqlalchemy import select

from auth.security import verify_password
from models.user import User, UserRole
from scripts.bootstrap_admin import bootstrap_admin


async def test_creates_the_admin_user_when_none_exists(db_session):
    user = await bootstrap_admin(
        db_session, username="v2-prod-admin", password="a-real-chosen-password", display_name="Responsabile V2"
    )

    assert user is not None
    assert user.id is not None
    assert user.role == UserRole.admin
    assert user.active is True
    assert verify_password("a-real-chosen-password", user.password_hash)


async def test_second_run_does_not_overwrite_the_existing_user(db_session):
    first = await bootstrap_admin(db_session, username="v2-prod-admin", password="password-uno", display_name="A")
    assert first is not None

    second = await bootstrap_admin(db_session, username="v2-prod-admin", password="password-due", display_name="B")
    assert second is None  # nessuna modifica: lo script segnala che l'utente esisteva gia'

    rows = (await db_session.execute(select(User).where(User.username == "v2-prod-admin"))).scalars().all()
    assert len(rows) == 1
    # La password del PRIMO run resta quella valida - il secondo run non l'ha toccata.
    assert verify_password("password-uno", rows[0].password_hash)
    assert not verify_password("password-due", rows[0].password_hash)


async def test_password_comes_from_the_given_argument_not_a_constant(db_session):
    user_a = await bootstrap_admin(db_session, username="v2-admin-a", password="password-alfa", display_name="A")
    user_b = await bootstrap_admin(db_session, username="v2-admin-b", password="password-beta", display_name="B")

    assert verify_password("password-alfa", user_a.password_hash)
    assert verify_password("password-beta", user_b.password_hash)
    assert not verify_password("password-beta", user_a.password_hash)
    assert not verify_password("password-alfa", user_b.password_hash)
