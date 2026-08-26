from httpx import AsyncClient


async def test_create_veterinarian_with_hours(authed_client: AsyncClient):
    response = await authed_client.post(
        "/api/veterinarians",
        json={
            "clinic_name": "Ambulatorio Test",
            "hours": [
                {"day_of_week": 0, "closed": False, "morning_start": "09:00:00", "morning_end": "12:00:00"},
                {"day_of_week": 6, "closed": True},
            ],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["hours"]) == 2


async def test_create_veterinarian_with_invalid_day_returns_422(authed_client: AsyncClient):
    response = await authed_client.post(
        "/api/veterinarians", json={"clinic_name": "X", "hours": [{"day_of_week": 9, "closed": False}]}
    )
    assert response.status_code == 422


async def test_create_veterinarian_without_any_name_returns_422(authed_client: AsyncClient):
    response = await authed_client.post("/api/veterinarians", json={"hours": []})
    assert response.status_code == 422


async def test_veterinarian_status_field_is_not_a_thing_yet_but_active_is_not_client_settable(
    authed_client: AsyncClient,
):
    """Coerenza con la regola generale doc09: i campi di stato/attivazione
    non sono mai esposti dagli schemi di creazione/aggiornamento - solo
    dall'azione dedicata di disattivazione."""
    response = await authed_client.post("/api/veterinarians", json={"clinic_name": "X", "active": False})
    assert response.status_code == 201
    assert response.json()["active"] is True
