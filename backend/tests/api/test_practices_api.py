from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


def _payload(sample_client, sample_location, **overrides):
    base = {
        "client_id": sample_client.id,
        "destination_branch_id": sample_location.id,
        "request_origin": "Collaboratore",
        "service_type": "Cremazione singola",
    }
    base.update(overrides)
    return base


async def test_list_practices_requires_authentication(client: AsyncClient):
    response = await client.get("/api/practices")
    assert response.status_code == 401


async def test_create_practice_returns_201_and_status_ritirato(
    authed_client: AsyncClient, sample_client, sample_location
):
    response = await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ritirato"
    assert body["practice_number"].startswith("COL-")


async def test_create_practice_ignores_a_client_supplied_status_field(
    authed_client: AsyncClient, sample_client, sample_location
):
    """doc09 'lo stato iniziale non e' mai un parametro di creazione' -
    anche se un chiamante malevolo/ingenuo lo invia, non ha alcun effetto:
    lo schema PracticeCreate non ha un campo status."""
    payload = _payload(sample_client, sample_location)
    payload["status"] = "consegnato"
    response = await authed_client.post("/api/practices", json=payload)
    assert response.status_code == 201
    assert response.json()["status"] == "ritirato"


async def test_create_practice_rejects_invalid_service_type_with_422(
    authed_client: AsyncClient, sample_client, sample_location
):
    response = await authed_client.post(
        "/api/practices", json=_payload(sample_client, sample_location, service_type="Non valido")
    )
    assert response.status_code == 422


async def test_create_practice_rejects_percorso_a_origin_with_422(
    authed_client: AsyncClient, sample_client, sample_location
):
    response = await authed_client.post(
        "/api/practices", json=_payload(sample_client, sample_location, request_origin="Privato")
    )
    assert response.status_code == 422


async def test_get_nonexistent_practice_returns_404(authed_client: AsyncClient):
    response = await authed_client.get("/api/practices/999999")
    assert response.status_code == 404


async def test_operator_can_perform_workflow_transition(client: AsyncClient, db_session, operator_user: User, sample_client, sample_location):
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        create = await client.post("/api/practices", json=_payload(sample_client, sample_location))
        practice_id = create.json()["id"]

        transition = await client.post(f"/api/practices/{practice_id}/transition", json={"target_status": "in_programma"})
        assert transition.status_code == 200
        assert transition.json()["status"] == "in_programma"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_operator_cannot_correct_state_gets_403(client: AsyncClient, db_session, operator_user: User, sample_client, sample_location):
    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        create = await client.post("/api/practices", json=_payload(sample_client, sample_location))
        practice_id = create.json()["id"]

        correction = await client.post(
            f"/api/practices/{practice_id}/correct-state",
            json={"target_status": "cremato", "reason": "tentativo operatore"},
        )
        assert correction.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_admin_can_correct_state_with_reason(authed_client: AsyncClient, sample_client, sample_location):
    create = await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))
    practice_id = create.json()["id"]

    correction = await authed_client.post(
        f"/api/practices/{practice_id}/correct-state",
        json={"target_status": "cremato", "reason": "dato storico migrato"},
    )
    assert correction.status_code == 200
    assert correction.json()["status"] == "cremato"


async def test_admin_correction_without_reason_returns_422(authed_client: AsyncClient, sample_client, sample_location):
    create = await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))
    practice_id = create.json()["id"]

    correction = await authed_client.post(f"/api/practices/{practice_id}/correct-state", json={"target_status": "cremato"})
    assert correction.status_code == 422  # Pydantic: reason mancante nello schema


async def test_invalid_workflow_jump_returns_409(authed_client: AsyncClient, sample_client, sample_location):
    create = await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))
    practice_id = create.json()["id"]

    response = await authed_client.post(f"/api/practices/{practice_id}/transition", json={"target_status": "cremato"})
    assert response.status_code == 409


async def test_trash_hides_practice_from_list(authed_client: AsyncClient, sample_client, sample_location):
    create = await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))
    practice_id = create.json()["id"]

    trash = await authed_client.post(f"/api/practices/{practice_id}/trash", json={"reason": "test"})
    assert trash.status_code == 200

    listing = await authed_client.get("/api/practices")
    assert practice_id not in [p["id"] for p in listing.json()]


async def test_list_practices_search_by_practice_number(authed_client: AsyncClient, sample_client, sample_location):
    create = await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))
    number = create.json()["practice_number"]

    found = await authed_client.get("/api/practices", params={"q": number})
    assert any(p["practice_number"] == number for p in found.json())

    not_found = await authed_client.get("/api/practices", params={"q": "NUMERO-CHE-NON-ESISTE"})
    assert not_found.json() == []


async def test_list_practices_filter_by_status(authed_client: AsyncClient, sample_client, sample_location):
    create = await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))
    practice_id = create.json()["id"]

    matching = await authed_client.get("/api/practices", params={"status": "ritirato"})
    assert practice_id in [p["id"] for p in matching.json()]

    non_matching = await authed_client.get("/api/practices", params={"status": "consegnato"})
    assert practice_id not in [p["id"] for p in non_matching.json()]


async def test_list_practices_pagination(authed_client: AsyncClient, sample_client, sample_location):
    for _ in range(3):
        await authed_client.post("/api/practices", json=_payload(sample_client, sample_location))

    page1 = await authed_client.get("/api/practices", params={"limit": 2, "offset": 0})
    page2 = await authed_client.get("/api/practices", params={"limit": 2, "offset": 2})
    assert len(page1.json()) == 2
    ids_page1 = {p["id"] for p in page1.json()}
    ids_page2 = {p["id"] for p in page2.json()}
    assert ids_page1.isdisjoint(ids_page2)
