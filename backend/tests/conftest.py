"""doc09 'Testing': repositories/services testati contro un Postgres
reale (non SQLite-in-memoria), per non nascondere differenze di
dialetto. Ogni test gira in una propria transazione con SAVEPOINT,
fatta rollback alla fine - anche se il codice sotto test chiama
db.commit(), l'isolamento tra i test resta garantito."""

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://ppm_v2:ppm_v2_dev_password@localhost:5432/ppm_v2_test"
)

from api.dependencies import get_current_user  # noqa: E402
from auth.security import hash_password  # noqa: E402
from database import get_session  # noqa: E402
from main import app  # noqa: E402
from models.calendar_zone import CalendarZone  # noqa: E402
from models.client import Client  # noqa: E402
from models.company_location import CompanyLocation  # noqa: E402
from models.user import User, UserRole  # noqa: E402

test_engine = create_async_engine(os.environ["DATABASE_URL"])


@pytest_asyncio.fixture
async def db_session():
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        username="test-admin",
        password_hash=hash_password("test-password"),
        display_name="Test Admin",
        role=UserRole.admin,
        active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def operator_user(db_session: AsyncSession) -> User:
    user = User(
        username="test-operator",
        password_hash=hash_password("test-password"),
        display_name="Test Operator",
        role=UserRole.operator,
        active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def sample_client(db_session: AsyncSession) -> Client:
    """Cliente reale minimo, richiesto da practices.client_id NOT NULL."""
    client = Client(first_name="Mario", last_name="Rossi", phone="333123456")
    db_session.add(client)
    await db_session.flush()
    return client


@pytest_asyncio.fixture
async def sample_location(db_session: AsyncSession) -> CompanyLocation:
    """Sede aziendale reale minima, richiesta da practices.destination_branch_id
    NOT NULL - doc06 Addendum C (Livorno/Empoli sono i valori reali, ma il
    nome esatto non conta per i test di dominio)."""
    location = CompanyLocation(name="Sede di test", has_cremation_plant=True)
    db_session.add(location)
    await db_session.flush()
    return location


@pytest_asyncio.fixture
async def sample_zone(db_session: AsyncSession) -> CalendarZone:
    """Zona geografica reale minima, richiesta da pickup_type='domicilio'
    (doc06 Addendum C)."""
    zone = CalendarZone(name="Zona di test")
    db_session.add(zone)
    await db_session.flush()
    return zone


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    """Client HTTP asincrono contro l'app FastAPI reale, con get_session
    rimappato sulla stessa sessione/transazione del test (stesso principio
    di isolamento del fixture db_session)."""

    async def _override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, admin_user: User):
    """Client gia' autenticato come admin, bypassando il login HTTP (piu'
    veloce e isola i test funzionali dal test del login stesso)."""

    async def _override_current_user():
        return admin_user

    app.dependency_overrides[get_current_user] = _override_current_user
    yield client
    app.dependency_overrides.pop(get_current_user, None)
