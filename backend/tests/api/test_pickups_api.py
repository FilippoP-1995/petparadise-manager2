from datetime import datetime, timedelta, timezone

from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


def _start_end():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return start.isoformat(), (start + timedelta(hours=1)).isoformat()


def _payload(sample_client, sample_zone, **overrides):
    start, end = _start_end()
    base = {
        "start_at": start,
        "end_at": end,
        "client_id": sample_client.id,
        "pickup_type": "domicilio",
        "pickup_zone_id": sample_zone.id,
    }
    base.update(overrides)
    return base


async def test_list_pickups_requires_authentication(client: AsyncClient):
    response = await client.get("/api/pickups")
    assert response.status_code == 401


async def test_create_pickup_returns_201_and_da_confermare(authed_client: AsyncClient, sample_client, sample_zone):
    response = await authed_client.post("/api/pickups", json=_payload(sample_client, sample_zone))
    assert response.status_code == 201
    assert response.json()["pickup_status"] == "da_confermare"


async def test_create_pickup_rejects_inconsistent_fields_with_422(authed_client: AsyncClient, sample_client):
    response = await authed_client.post(
        "/api/pickups",
        json=_payload(sample_client, sample_client, pickup_type="sede_aziendale", pickup_zone_id=None, pickup_location_id=None),
    )
    assert response.status_code == 422


async def test_operator_can_transition_pickup(client: AsyncClient, operator_user: User, sample_client, sample_zone):
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        create = await client.post("/api/pickups", json=_payload(sample_client, sample_zone))
        pickup_id = create.json()["id"]

        response = await client.post(f"/api/pickups/{pickup_id}/transition", json={"target_status": "da_ritirare"})
        assert response.status_code == 200
        assert response.json()["pickup_status"] == "da_ritirare"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_operator_can_cancel_pickup(client: AsyncClient, operator_user: User, sample_client, sample_zone):
    """Sezione 7: Operator+Admin, non riservata all'Admin."""
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        create = await client.post("/api/pickups", json=_payload(sample_client, sample_zone))
        pickup_id = create.json()["id"]

        response = await client.post(f"/api/pickups/{pickup_id}/cancel", json={"reason": "cliente ha annullato"})
        assert response.status_code == 200
        assert response.json()["pickup_status"] == "annullato"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_invalid_transition_returns_409(authed_client: AsyncClient, sample_client, sample_zone):
    create = await authed_client.post("/api/pickups", json=_payload(sample_client, sample_zone))
    pickup_id = create.json()["id"]

    response = await authed_client.post(f"/api/pickups/{pickup_id}/transition", json={"target_status": "ritirato"})
    assert response.status_code == 409


async def test_annullato_transition_is_forever_rejected(authed_client: AsyncClient, sample_client, sample_zone):
    create = await authed_client.post("/api/pickups", json=_payload(sample_client, sample_zone))
    pickup_id = create.json()["id"]
    await authed_client.post(f"/api/pickups/{pickup_id}/cancel", json={"reason": "test"})

    response = await authed_client.post(f"/api/pickups/{pickup_id}/transition", json={"target_status": "da_ritirare"})
    assert response.status_code == 409


async def test_full_flow_create_practice_from_pickup(authed_client: AsyncClient, sample_client, sample_zone, sample_location):
    create = await authed_client.post("/api/pickups", json=_payload(sample_client, sample_zone))
    pickup_id = create.json()["id"]
    await authed_client.post(f"/api/pickups/{pickup_id}/transition", json={"target_status": "da_ritirare"})
    await authed_client.post(f"/api/pickups/{pickup_id}/transition", json={"target_status": "ritirato"})

    response = await authed_client.post(
        f"/api/pickups/{pickup_id}/create-practice", json={"destination_branch_id": sample_location.id}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ritirato"
    assert body["originating_pickup_event_id"] == pickup_id

    pickup_after = await authed_client.get(f"/api/pickups/{pickup_id}")
    assert pickup_after.json()["linked_practice_id"] == body["id"]


async def test_cancel_and_trash_practice_requires_reason(authed_client: AsyncClient, sample_client, sample_zone, sample_location):
    create = await authed_client.post("/api/pickups", json=_payload(sample_client, sample_zone))
    pickup_id = create.json()["id"]
    await authed_client.post(f"/api/pickups/{pickup_id}/transition", json={"target_status": "da_ritirare"})
    await authed_client.post(f"/api/pickups/{pickup_id}/transition", json={"target_status": "ritirato"})
    await authed_client.post(f"/api/pickups/{pickup_id}/create-practice", json={"destination_branch_id": sample_location.id})

    missing_reason = await authed_client.post(f"/api/pickups/{pickup_id}/cancel-and-trash-practice", json={"reason": ""})
    assert missing_reason.status_code == 422

    ok = await authed_client.post(f"/api/pickups/{pickup_id}/cancel-and-trash-practice", json={"reason": "richiesta cliente"})
    assert ok.status_code == 200
    assert ok.json()["pickup_status"] == "annullato"


async def test_list_pickups_search_and_pagination(authed_client: AsyncClient, sample_client, sample_zone):
    for _ in range(3):
        await authed_client.post("/api/pickups", json=_payload(sample_client, sample_zone))

    page1 = await authed_client.get("/api/pickups", params={"limit": 2, "offset": 0})
    page2 = await authed_client.get("/api/pickups", params={"limit": 2, "offset": 2})
    assert len(page1.json()) == 2
    ids1 = {p["id"] for p in page1.json()}
    ids2 = {p["id"] for p in page2.json()}
    assert ids1.isdisjoint(ids2)

    filtered = await authed_client.get("/api/pickups", params={"status": "da_confermare"})
    assert all(p["pickup_status"] == "da_confermare" for p in filtered.json())
