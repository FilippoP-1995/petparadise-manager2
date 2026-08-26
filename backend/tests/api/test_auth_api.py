from httpx import AsyncClient

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
