from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


async def test_list_locations_requires_authentication(client: AsyncClient):
    response = await client.get("/api/company-locations")
    assert response.status_code == 401


async def test_admin_can_create_location(authed_client: AsyncClient):
    response = await authed_client.post("/api/company-locations", json={"name": "Firenze", "has_cremation_plant": False})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Firenze"
    assert body["active"] is True


async def test_operator_cannot_create_location(client: AsyncClient, operator_user: User):
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        response = await client.post("/api/company-locations", json={"name": "Firenze"})
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_operator_can_list_but_not_modify(client: AsyncClient, operator_user: User):
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        response = await client.get("/api/company-locations")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_admin_can_update_and_deactivate_location(authed_client: AsyncClient):
    create = await authed_client.post("/api/company-locations", json={"name": "Firenze"})
    location_id = create.json()["id"]

    update = await authed_client.put(
        f"/api/company-locations/{location_id}", json={"name": "Firenze Nord", "has_cremation_plant": True}
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Firenze Nord"

    deactivate = await authed_client.post(f"/api/company-locations/{location_id}/disattiva")
    assert deactivate.status_code == 200
    assert deactivate.json()["active"] is False


async def test_operator_cannot_deactivate_location(client: AsyncClient, operator_user: User, admin_user: User):
    async def _as_admin():
        return admin_user

    app.dependency_overrides[get_current_user] = _as_admin
    create = await client.post("/api/company-locations", json={"name": "Empoli Sud"})
    location_id = create.json()["id"]

    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        response = await client.post(f"/api/company-locations/{location_id}/disattiva")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_create_location_rejects_empty_name_with_422(authed_client: AsyncClient):
    response = await authed_client.post("/api/company-locations", json={"name": "   "})
    assert response.status_code == 422


async def test_get_unknown_location_returns_404(authed_client: AsyncClient):
    response = await authed_client.get("/api/company-locations/999999")
    assert response.status_code == 404
