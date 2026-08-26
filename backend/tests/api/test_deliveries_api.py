from datetime import datetime, timedelta, timezone

from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


def _start_end():
    start = datetime.now(timezone.utc) + timedelta(days=1)
    return start.isoformat(), (start + timedelta(hours=1)).isoformat()


def _payload(**overrides):
    start, end = _start_end()
    base = {"start_at": start, "end_at": end, "delivery_type": "sede_aziendale"}
    base.update(overrides)
    return base


async def test_list_deliveries_requires_authentication(client: AsyncClient):
    response = await client.get("/api/deliveries")
    assert response.status_code == 401


async def test_create_delivery_in_sede(authed_client: AsyncClient, sample_location):
    response = await authed_client.post("/api/deliveries", json=_payload(delivery_location_id=sample_location.id))
    assert response.status_code == 201
    assert response.json()["delivery_type"] == "sede_aziendale"


async def test_create_delivery_fuori_sede_domicilio(authed_client: AsyncClient, sample_zone):
    response = await authed_client.post(
        "/api/deliveries", json=_payload(delivery_type="domicilio", delivery_zone_id=sample_zone.id)
    )
    assert response.status_code == 201
    assert response.json()["delivery_zone_id"] == sample_zone.id


async def test_create_delivery_missing_required_field_returns_422(authed_client: AsyncClient):
    response = await authed_client.post("/api/deliveries", json=_payload(delivery_type="sede_aziendale", delivery_location_id=None))
    assert response.status_code == 422


async def test_operator_can_create_and_link_delivery(
    client: AsyncClient, operator_user: User, sample_client, sample_location
):
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        create_practice = await client.post(
            "/api/practices",
            json={
                "client_id": sample_client.id,
                "destination_branch_id": sample_location.id,
                "request_origin": "Collaboratore",
                "service_type": "Cremazione singola",
                "line_items": [{"category": "cremazione", "description": "Cremazione", "amount_cents": 10000}],
            },
        )
        practice_id = create_practice.json()["id"]

        create_delivery = await client.post(
            "/api/deliveries", json=_payload(delivery_location_id=sample_location.id, preliminary_payment_amount=10000)
        )
        delivery_id = create_delivery.json()["id"]

        link = await client.post(f"/api/deliveries/{delivery_id}/link-practice", json={"practice_id": practice_id})
        assert link.status_code == 200
        assert link.json()["linked_practice_id"] == practice_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_link_delivery_mismatch_returns_422_without_confirmation(authed_client: AsyncClient, sample_client, sample_location):
    create_practice = await authed_client.post(
        "/api/practices",
        json={
            "client_id": sample_client.id,
            "destination_branch_id": sample_location.id,
            "request_origin": "Collaboratore",
            "service_type": "Cremazione singola",
            "line_items": [{"category": "cremazione", "description": "Cremazione", "amount_cents": 10000}],
        },
    )
    practice_id = create_practice.json()["id"]

    create_delivery = await authed_client.post(
        "/api/deliveries", json=_payload(delivery_location_id=sample_location.id, preliminary_payment_amount=1)
    )
    delivery_id = create_delivery.json()["id"]

    response = await authed_client.post(f"/api/deliveries/{delivery_id}/link-practice", json={"practice_id": practice_id})
    assert response.status_code == 422


async def test_list_deliveries_search_and_pagination(authed_client: AsyncClient, sample_location):
    for _ in range(3):
        await authed_client.post("/api/deliveries", json=_payload(delivery_location_id=sample_location.id))

    page1 = await authed_client.get("/api/deliveries", params={"limit": 2, "offset": 0})
    page2 = await authed_client.get("/api/deliveries", params={"limit": 2, "offset": 2})
    assert len(page1.json()) == 2
    ids1 = {d["id"] for d in page1.json()}
    ids2 = {d["id"] for d in page2.json()}
    assert ids1.isdisjoint(ids2)


async def test_list_deliveries_filters_by_date_range(authed_client: AsyncClient, sample_location):
    in_three_days = datetime.now(timezone.utc) + timedelta(days=3)
    far_payload = _payload(
        delivery_location_id=sample_location.id,
        start_at=in_three_days.isoformat(),
        end_at=(in_three_days + timedelta(hours=1)).isoformat(),
    )

    near = await authed_client.post("/api/deliveries", json=_payload(delivery_location_id=sample_location.id))
    far = await authed_client.post("/api/deliveries", json=far_payload)

    day_before = datetime.now(timezone.utc)
    day_after = datetime.now(timezone.utc) + timedelta(days=2)
    response = await authed_client.get(
        "/api/deliveries",
        params={"date_from": day_before.isoformat(), "date_to": day_after.isoformat()},
    )
    ids = {d["id"] for d in response.json()}
    assert near.json()["id"] in ids
    assert far.json()["id"] not in ids
