from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


def _payload(**overrides):
    base = {"category": "Urna", "name": "Urna in noce", "material": "Legno", "price_cents": 15000, "quantity": 5}
    base.update(overrides)
    return base


async def test_list_urns_requires_authentication(client: AsyncClient):
    response = await client.get("/api/urns")
    assert response.status_code == 401


async def test_create_urn_returns_201_with_generated_code(authed_client: AsyncClient):
    response = await authed_client.post("/api/urns", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["internal_code"].startswith("URN-")
    assert body["active"] is True


async def test_operator_can_create_urn(client: AsyncClient, operator_user: User):
    """FACT V1 (save_urn): nessuna restrizione di ruolo - Operator+Admin."""

    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        response = await client.post("/api/urns", json=_payload())
        assert response.status_code == 201
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_create_urn_rejects_negative_price_with_422(authed_client: AsyncClient):
    response = await authed_client.post("/api/urns", json=_payload(price_cents=-1))
    assert response.status_code == 422


async def test_update_urn_and_list_movements(authed_client: AsyncClient):
    create = await authed_client.post("/api/urns", json=_payload(quantity=5))
    urn_id = create.json()["id"]

    update = await authed_client.put(f"/api/urns/{urn_id}", json=_payload(quantity=8))
    assert update.status_code == 200
    assert update.json()["quantity"] == 8

    movements = await authed_client.get(f"/api/urns/{urn_id}/movements")
    assert movements.status_code == 200
    types = [m["movement_type"] for m in movements.json()]
    assert "Creazione / carico iniziale" in types
    assert "Rettifica manuale" in types


async def test_urn_category_stats(authed_client: AsyncClient):
    """V1 (urn_catalog_page: barra Modelli/Pezzi disponibili/Esaurite/
    Scorte basse/Valore magazzino) - qui verificato che la stessa esatta
    formula (out=scorta<=0, low=scorta positiva ma <=soglia, mai
    sovrapposti) sia riprodotta dalla query aggregata."""
    await authed_client.post("/api/urns", json=_payload(name="Disponibile", price_cents=10000, quantity=10, low_stock_threshold=3))
    await authed_client.post("/api/urns", json=_payload(name="Scorta bassa", price_cents=5000, quantity=2, low_stock_threshold=3))
    await authed_client.post("/api/urns", json=_payload(name="Esaurita", price_cents=8000, quantity=0, low_stock_threshold=3))
    inactive = await authed_client.post("/api/urns", json=_payload(name="Disattivata", price_cents=1000, quantity=100))
    await authed_client.post(f"/api/urns/{inactive.json()['id']}/disattiva")

    response = await authed_client.get("/api/urns/stats", params={"category": "Urna"})
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == 3
    assert body["quantity"] == 12
    assert body["out"] == 1
    assert body["low"] == 1
    assert body["value_cents"] == 10 * 10000 + 2 * 5000


async def test_urn_category_stats_requires_authentication(client: AsyncClient):
    response = await client.get("/api/urns/stats", params={"category": "Urna"})
    assert response.status_code == 401


async def test_deactivate_urn(authed_client: AsyncClient):
    create = await authed_client.post("/api/urns", json=_payload())
    urn_id = create.json()["id"]

    response = await authed_client.post(f"/api/urns/{urn_id}/disattiva")
    assert response.status_code == 200
    assert response.json()["active"] is False


async def test_list_urns_filters_by_category_and_active(authed_client: AsyncClient):
    await authed_client.post("/api/urns", json=_payload(category="Urna", name="Urna A"))
    accessorio = await authed_client.post("/api/urns", json=_payload(category="Accessorio", name="Accessorio A"))
    await authed_client.post(f"/api/urns/{accessorio.json()['id']}/disattiva")

    only_urne = await authed_client.get("/api/urns", params={"category": "Urna"})
    assert all(u["category"] == "Urna" for u in only_urne.json())

    all_including_inactive = await authed_client.get("/api/urns", params={"active_only": "false"})
    assert any(u["id"] == accessorio.json()["id"] for u in all_including_inactive.json())

    active_only = await authed_client.get("/api/urns", params={"active_only": "true"})
    assert all(u["id"] != accessorio.json()["id"] for u in active_only.json())


async def test_get_unknown_urn_returns_404(authed_client: AsyncClient):
    response = await authed_client.get("/api/urns/999999")
    assert response.status_code == 404


async def test_list_urns_search_by_name_code_or_material(authed_client: AsyncClient):
    await authed_client.post("/api/urns", json=_payload(name="Urna in noce", material="Legno"))
    other = await authed_client.post("/api/urns", json=_payload(name="Urna in ottone", material="Ottone"))

    by_name = await authed_client.get("/api/urns", params={"q": "noce"})
    assert [u["name"] for u in by_name.json()] == ["Urna in noce"]

    by_material = await authed_client.get("/api/urns", params={"q": "ottone"})
    assert [u["id"] for u in by_material.json()] == [other.json()["id"]]

    by_code = await authed_client.get("/api/urns", params={"q": other.json()["internal_code"]})
    assert [u["id"] for u in by_code.json()] == [other.json()["id"]]


async def test_list_urns_respects_limit_and_offset(authed_client: AsyncClient):
    for i in range(3):
        await authed_client.post("/api/urns", json=_payload(name=f"Urna paginata {i}"))

    page = await authed_client.get("/api/urns", params={"limit": 2, "offset": 0})
    assert len(page.json()) == 2

    next_page = await authed_client.get("/api/urns", params={"limit": 2, "offset": 2})
    assert len(next_page.json()) >= 1
    assert {u["id"] for u in page.json()}.isdisjoint({u["id"] for u in next_page.json()})
