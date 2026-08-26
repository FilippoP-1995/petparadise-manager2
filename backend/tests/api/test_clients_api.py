from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


async def test_create_and_get_client(authed_client: AsyncClient):
    create = await authed_client.post("/api/clients", json={"first_name": "Mario", "last_name": "Rossi"})
    assert create.status_code == 201
    client_id = create.json()["id"]

    get_resp = await authed_client.get(f"/api/clients/{client_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["first_name"] == "Mario"


async def test_create_client_without_identifying_field_returns_422(authed_client: AsyncClient):
    response = await authed_client.post("/api/clients", json={"phone": "333"})
    assert response.status_code == 422


async def test_get_nonexistent_client_returns_404(authed_client: AsyncClient):
    response = await authed_client.get("/api/clients/999999")
    assert response.status_code == 404


async def test_list_clients_requires_authentication(client: AsyncClient):
    response = await client.get("/api/clients")
    assert response.status_code == 401


async def test_deactivate_client_requires_admin_role(client: AsyncClient, db_session, operator_user: User):
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        create = await client.post("/api/clients", json={"first_name": "Anna", "last_name": "Verdi"})
        assert create.status_code == 201
        client_id = create.json()["id"]

        deactivate = await client.post(f"/api/clients/{client_id}/disattiva")
        assert deactivate.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_deactivate_client_succeeds_for_admin(authed_client: AsyncClient):
    create = await authed_client.post("/api/clients", json={"first_name": "Luca", "last_name": "Neri"})
    client_id = create.json()["id"]

    deactivate = await authed_client.post(f"/api/clients/{client_id}/disattiva")
    assert deactivate.status_code == 200
    assert deactivate.json()["active"] is False

    listing = await authed_client.get("/api/clients")
    assert client_id not in [c["id"] for c in listing.json()]
