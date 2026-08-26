from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


def _cycle_payload(**overrides):
    base = {
        "cycle_date": "2026-09-10",
        "planned_start": "09:00:00",
        "planned_end": "10:30:00",
    }
    base.update(overrides)
    return base


def _practice_payload(sample_client, sample_location, animal_names, **overrides):
    base = {
        "client_id": sample_client.id,
        "destination_branch_id": sample_location.id,
        "request_origin": "Collaboratore",
        "service_type": "Cremazione singola",
        "animals": [{"name": n} for n in animal_names],
    }
    base.update(overrides)
    return base


async def _create_eligible_practice_and_animal(authed_client: AsyncClient, sample_client, sample_location):
    create = await authed_client.post("/api/practices", json=_practice_payload(sample_client, sample_location, ["Fido"]))
    practice = create.json()
    await authed_client.post(f"/api/practices/{practice['id']}/transition", json={"target_status": "in_programma"})
    return practice["id"], practice["animals"][0]["id"]


async def test_list_cycles_requires_authentication(client: AsyncClient):
    response = await client.get("/api/cremation-cycles")
    assert response.status_code == 401


async def test_create_cycle_returns_201_and_pianificato(authed_client: AsyncClient):
    response = await authed_client.post("/api/cremation-cycles", json=_cycle_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pianificato"
    assert body["animals"] == []


async def test_create_cycle_ignores_client_supplied_status(authed_client: AsyncClient):
    """Stato iniziale mai un parametro di creazione (doc09), come per Pratica/Ritiro."""
    payload = _cycle_payload()
    payload["status"] = "completato"
    response = await authed_client.post("/api/cremation-cycles", json=payload)
    assert response.status_code == 201
    assert response.json()["status"] == "pianificato"


async def test_get_cycle_not_found_returns_404(authed_client: AsyncClient):
    response = await authed_client.get("/api/cremation-cycles/999999")
    assert response.status_code == 404


async def test_eligible_animals_route_does_not_collide_with_cycle_id_route(authed_client: AsyncClient):
    """/eligible-animals deve essere risolta come route statica, non come
    /{cycle_id} con cycle_id='eligible-animals'."""
    response = await authed_client.get("/api/cremation-cycles/eligible-animals")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_assign_and_remove_animal_full_flow(authed_client: AsyncClient, sample_client, sample_location):
    practice_id, animal_id = await _create_eligible_practice_and_animal(authed_client, sample_client, sample_location)
    cycle = (await authed_client.post("/api/cremation-cycles", json=_cycle_payload())).json()

    assign = await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/assign-animal", json={"animal_id": animal_id})
    assert assign.status_code == 200
    body = assign.json()
    assert body["status"] == "in_attesa"
    assert [a["id"] for a in body["animals"]] == [animal_id]
    assert body["animals"][0]["practice_id"] == practice_id

    remove = await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/remove-animal", json={"animal_id": animal_id})
    assert remove.status_code == 200
    assert remove.json()["status"] == "pianificato"
    assert remove.json()["animals"] == []


async def test_assign_third_animal_returns_422(authed_client: AsyncClient, sample_client, sample_location):
    cycle = (await authed_client.post("/api/cremation-cycles", json=_cycle_payload())).json()
    create = await authed_client.post(
        "/api/practices", json=_practice_payload(sample_client, sample_location, ["A", "B", "C"])
    )
    practice = create.json()
    await authed_client.post(f"/api/practices/{practice['id']}/transition", json={"target_status": "in_programma"})
    a, b, c = [an["id"] for an in practice["animals"]]

    await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/assign-animal", json={"animal_id": a})
    await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/assign-animal", json={"animal_id": b})
    response = await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/assign-animal", json={"animal_id": c})
    assert response.status_code == 422


async def test_assign_animal_to_unknown_cycle_returns_404(authed_client: AsyncClient, sample_client, sample_location):
    _, animal_id = await _create_eligible_practice_and_animal(authed_client, sample_client, sample_location)
    response = await authed_client.post("/api/cremation-cycles/999999/assign-animal", json={"animal_id": animal_id})
    assert response.status_code == 404


async def test_complete_and_revert_full_flow(authed_client: AsyncClient, sample_client, sample_location):
    practice_id, animal_id = await _create_eligible_practice_and_animal(authed_client, sample_client, sample_location)
    cycle = (await authed_client.post("/api/cremation-cycles", json=_cycle_payload())).json()
    await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/assign-animal", json={"animal_id": animal_id})

    complete = await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/complete")
    assert complete.status_code == 200
    assert complete.json()["status"] == "completato"

    practice_after = await authed_client.get(f"/api/practices/{practice_id}")
    assert practice_after.json()["status"] == "cremato"

    reassign_blocked = await authed_client.post(
        f"/api/cremation-cycles/{cycle['id']}/remove-animal", json={"animal_id": animal_id}
    )
    assert reassign_blocked.status_code == 422, "un ciclo completato e' un record storico: non riassegnabile direttamente"

    revert = await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/revert", json={"reason": "errore operatore"})
    assert revert.status_code == 200
    assert revert.json()["status"] == "in_attesa"

    practice_reverted = await authed_client.get(f"/api/practices/{practice_id}")
    assert practice_reverted.json()["status"] == "in_programma"


async def test_revert_without_reason_returns_422(authed_client: AsyncClient, sample_client, sample_location):
    _, animal_id = await _create_eligible_practice_and_animal(authed_client, sample_client, sample_location)
    cycle = (await authed_client.post("/api/cremation-cycles", json=_cycle_payload())).json()
    await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/assign-animal", json={"animal_id": animal_id})
    await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/complete")

    response = await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/revert", json={"reason": ""})
    assert response.status_code == 422


async def test_complete_cycle_without_animals_returns_409(authed_client: AsyncClient):
    cycle = (await authed_client.post("/api/cremation-cycles", json=_cycle_payload())).json()
    response = await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/complete")
    assert response.status_code == 409


async def test_delete_completed_cycle_returns_422(authed_client: AsyncClient, sample_client, sample_location):
    _, animal_id = await _create_eligible_practice_and_animal(authed_client, sample_client, sample_location)
    cycle = (await authed_client.post("/api/cremation-cycles", json=_cycle_payload())).json()
    await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/assign-animal", json={"animal_id": animal_id})
    await authed_client.post(f"/api/cremation-cycles/{cycle['id']}/complete")

    response = await authed_client.delete(f"/api/cremation-cycles/{cycle['id']}")
    assert response.status_code == 422


async def test_delete_empty_cycle_returns_204(authed_client: AsyncClient):
    cycle = (await authed_client.post("/api/cremation-cycles", json=_cycle_payload())).json()
    response = await authed_client.delete(f"/api/cremation-cycles/{cycle['id']}")
    assert response.status_code == 204
    assert (await authed_client.get(f"/api/cremation-cycles/{cycle['id']}")).status_code == 404


async def test_operator_can_manage_cycles(client: AsyncClient, operator_user: User, sample_client, sample_location):
    """Nessuna restrizione Admin-only qui: Operator+Admin, come Pratiche/Ritiri."""

    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        cycle = (await client.post("/api/cremation-cycles", json=_cycle_payload())).json()
        assert cycle["status"] == "pianificato"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_list_cycles_filters_by_status_and_paginates(authed_client: AsyncClient):
    for _ in range(3):
        await authed_client.post("/api/cremation-cycles", json=_cycle_payload())

    page1 = await authed_client.get("/api/cremation-cycles", params={"limit": 2, "offset": 0})
    page2 = await authed_client.get("/api/cremation-cycles", params={"limit": 2, "offset": 2})
    assert len(page1.json()) == 2
    ids1 = {c["id"] for c in page1.json()}
    ids2 = {c["id"] for c in page2.json()}
    assert ids1.isdisjoint(ids2)

    filtered = await authed_client.get("/api/cremation-cycles", params={"status": "pianificato"})
    assert all(c["status"] == "pianificato" for c in filtered.json())
