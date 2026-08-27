from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from models.login_attempt import LoginAttempt
from models.session import Session
from models.user import User


async def test_login_with_correct_credentials_sets_session_cookie(client: AsyncClient, admin_user: User):
    response = await client.post(
        "/api/auth/login", json={"username": admin_user.username, "password": "test-password"}
    )
    assert response.status_code == 200
    assert "ppm_v2_session" in response.cookies


async def test_login_with_wrong_password_is_rejected(client: AsyncClient, admin_user: User):
    response = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
    assert response.status_code == 401


async def test_login_with_unknown_username_is_rejected(client: AsyncClient):
    response = await client.post("/api/auth/login", json={"username": "nessuno", "password": "x"})
    assert response.status_code == 401


async def test_me_without_session_is_rejected(client: AsyncClient):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


async def test_full_login_then_authenticated_request_flow(client: AsyncClient, admin_user: User):
    login = await client.post(
        "/api/auth/login", json={"username": admin_user.username, "password": "test-password"}
    )
    assert login.status_code == 200

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


async def test_logout_invalidates_session_server_side(client: AsyncClient, admin_user: User):
    """Release hardening: prima del fix, il logout cancellava solo il
    cookie - la sessione restava valida lato server fino alla scadenza
    naturale. 1) login; 2) verifica sessione valida; 3) logout; 4)
    tentativo autenticato con la stessa sessione; 5) verifica rifiuto."""
    login = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "test-password"})
    assert login.status_code == 200
    session_token = login.cookies["ppm_v2_session"]

    me_before = await client.get("/api/auth/me")
    assert me_before.status_code == 200

    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204

    # Riutilizzo esplicito dello stesso token dopo il logout - non basta
    # affidarsi al cookie-jar del client (che il logout ha gia' cancellato):
    # simula un token rubato/copiato prima del logout.
    client.cookies.set("ppm_v2_session", session_token)
    me_after = await client.get("/api/auth/me")
    assert me_after.status_code == 401


async def test_logout_deletes_the_session_row(client: AsyncClient, admin_user: User, db_session: AsyncSession):
    login = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "test-password"})
    session_token = login.cookies["ppm_v2_session"]
    assert await db_session.get(Session, session_token) is not None

    await client.post("/api/auth/logout")

    assert await db_session.get(Session, session_token) is None


async def test_logout_without_any_session_is_rejected(client: AsyncClient):
    response = await client.post("/api/auth/logout")
    assert response.status_code == 401


async def test_logout_with_already_expired_session_is_rejected(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    """Una sessione scaduta (non solo assente) deve comportarsi come
    qualunque sessione non valida - il logout non deve ne' avere
    successo ne' sollevare un errore diverso dal solito 401."""
    expired = Session(
        id="expired-test-token",
        user_id=admin_user.id,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(expired)
    await db_session.flush()

    client.cookies.set("ppm_v2_session", "expired-test-token")
    response = await client.post("/api/auth/logout")
    assert response.status_code == 401


async def test_failed_attempts_within_threshold_still_return_401(client: AsyncClient, admin_user: User):
    """Sotto soglia (5): ogni tentativo fallito resta un 401 normale, non
    un 429."""
    for _ in range(4):
        response = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
        assert response.status_code == 401


async def test_exceeding_threshold_returns_429(client: AsyncClient, admin_user: User):
    for _ in range(5):
        response = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
        assert response.status_code == 401

    blocked = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
    assert blocked.status_code == 429

    # Anche con la password CORRETTA, il tentativo resta bloccato finche'
    # e' sopra soglia - il rate limit e' sull'endpoint, non sulla verifica
    # della password.
    still_blocked = await client.post(
        "/api/auth/login", json={"username": admin_user.username, "password": "test-password"}
    )
    assert still_blocked.status_code == 429


async def test_successful_login_resets_the_counter(client: AsyncClient, admin_user: User):
    for _ in range(4):
        await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})

    success = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "test-password"})
    assert success.status_code == 200

    # Dopo il successo, altri 4 fallimenti (sotto soglia) non devono
    # sommarsi ai 4 precedenti al reset.
    for _ in range(4):
        response = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
        assert response.status_code == 401


async def test_nonexistent_username_is_rate_limited_the_same_way(client: AsyncClient):
    """Stesso trattamento di un utente esistente con password sbagliata -
    il comportamento del rate limit non deve rivelare se lo username
    esiste o meno."""
    for _ in range(5):
        response = await client.post("/api/auth/login", json={"username": "utente-inesistente", "password": "x"})
        assert response.status_code == 401

    blocked = await client.post("/api/auth/login", json={"username": "utente-inesistente", "password": "x"})
    assert blocked.status_code == 429


async def test_rate_limit_key_is_not_bypassed_by_case_or_whitespace(client: AsyncClient, admin_user: User):
    """Nessun bypass banale: maiuscole/minuscole e spazi attorno allo
    username devono contare sulla stessa chiave."""
    variants = [admin_user.username.upper(), f"  {admin_user.username}  ", admin_user.username.capitalize()]
    for i in range(5):
        variant = variants[i % len(variants)]
        response = await client.post("/api/auth/login", json={"username": variant, "password": "wrong"})
        assert response.status_code == 401

    blocked = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
    assert blocked.status_code == 429


async def test_rate_limit_recovers_after_the_window_expires(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    for _ in range(5):
        await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})

    blocked = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
    assert blocked.status_code == 429

    # Simula il passaggio del tempo spostando indietro i tentativi
    # registrati, invece di dormire 15 minuti reali nel test.
    await db_session.execute(
        update(LoginAttempt)
        .where(LoginAttempt.username_key == admin_user.username.strip().lower())
        .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=16))
    )
    await db_session.flush()

    recovered = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "wrong"})
    assert recovered.status_code == 401  # non piu' 429: la finestra e' scorsa


async def test_logout_does_not_affect_the_login_rate_limit_counter(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    """Il logout non ha alcuna relazione con il rate limit di login (che
    riguarda solo i tentativi FALLITI di autenticazione)."""
    login = await client.post("/api/auth/login", json={"username": admin_user.username, "password": "test-password"})
    assert login.status_code == 200
    await client.post("/api/auth/logout")

    username_key = admin_user.username.strip().lower()
    remaining = await db_session.execute(
        LoginAttempt.__table__.select().where(LoginAttempt.username_key == username_key)
    )
    assert remaining.fetchall() == []  # il login riuscito aveva gia' azzerato il contatore, il logout non lo tocca
