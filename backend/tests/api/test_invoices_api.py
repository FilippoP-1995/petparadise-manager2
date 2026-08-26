from httpx import AsyncClient

from api.dependencies import get_current_user
from main import app
from models.user import User


def _practice_payload(sample_client, sample_location, **overrides):
    base = {
        "client_id": sample_client.id,
        "destination_branch_id": sample_location.id,
        "request_origin": "Collaboratore",
        "service_type": "Cremazione singola",
        "line_items": [{"category": "Cremazione", "description": "Cremazione singola", "amount_cents": 34000}],
    }
    base.update(overrides)
    return base


async def _create_practice(authed_client: AsyncClient, sample_client, sample_location):
    response = await authed_client.post("/api/practices", json=_practice_payload(sample_client, sample_location))
    return response.json()


def _invoice_payload(practice_id, **overrides):
    base = {"practice_id": practice_id, "invoice_number": "FT-API-0001", "total_amount_cents": 34000, "channel": "W"}
    base.update(overrides)
    return base


async def test_list_invoices_requires_authentication(client: AsyncClient):
    response = await client.get("/api/invoices")
    assert response.status_code == 401


async def test_create_invoice_returns_201(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    response = await authed_client.post("/api/invoices", json=_invoice_payload(practice["id"]))
    assert response.status_code == 201
    body = response.json()
    assert body["invoice_number"] == "FT-API-0001"
    assert body["practice_number_snapshot"] == practice["practice_number"]


async def test_create_invoice_rejects_collaboratori_channel_with_422(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    response = await authed_client.post("/api/invoices", json=_invoice_payload(practice["id"], channel="Collaboratori"))
    assert response.status_code == 422


async def test_create_invoice_duplicate_number_returns_422(authed_client: AsyncClient, sample_client, sample_location):
    practice1 = await _create_practice(authed_client, sample_client, sample_location)
    practice2 = await _create_practice(authed_client, sample_client, sample_location)
    await authed_client.post("/api/invoices", json=_invoice_payload(practice1["id"]))
    response = await authed_client.post("/api/invoices", json=_invoice_payload(practice2["id"]))
    assert response.status_code == 422


async def test_create_invoice_unknown_practice_returns_404(authed_client: AsyncClient):
    response = await authed_client.post("/api/invoices", json=_invoice_payload(999999))
    assert response.status_code == 404


async def test_operator_can_create_invoice(client: AsyncClient, operator_user: User, sample_client, sample_location, admin_user: User):
    """FACT V1: nessuna restrizione di ruolo su fatture/pagamenti."""

    async def _as_admin():
        return admin_user

    app.dependency_overrides[get_current_user] = _as_admin
    practice = await _create_practice(client, sample_client, sample_location)

    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        response = await client.post("/api/invoices", json=_invoice_payload(practice["id"], invoice_number="FT-OPERATOR-1"))
        assert response.status_code == 201
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_reconciliation_reflects_linked_payments(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    invoice = (await authed_client.post("/api/invoices", json=_invoice_payload(practice["id"]))).json()

    payment = (
        await authed_client.post(
            "/api/payments",
            json={
                "practice_id": practice["id"], "movement_date": "2026-01-01", "channel": "W",
                "ledger_section": "Entrata", "movement_type": "Acconto", "amount_cents": 10000,
            },
        )
    ).json()
    await authed_client.post(f"/api/invoices/{invoice['id']}/collega-pagamento", json={"payment_id": payment["id"]})

    recon = await authed_client.get(f"/api/invoices/{invoice['id']}/riconciliazione")
    assert recon.status_code == 200
    body = recon.json()
    assert body["paid_cents"] == 10000
    assert body["residual_cents"] == 24000
    assert body["status"] == "parziale"


async def test_get_unknown_invoice_returns_404(authed_client: AsyncClient):
    response = await authed_client.get("/api/invoices/999999")
    assert response.status_code == 404


async def test_list_invoices_filters_by_practice(authed_client: AsyncClient, sample_client, sample_location):
    practice1 = await _create_practice(authed_client, sample_client, sample_location)
    practice2 = await _create_practice(authed_client, sample_client, sample_location)
    await authed_client.post("/api/invoices", json=_invoice_payload(practice1["id"], invoice_number="FT-FILTER-1"))
    await authed_client.post("/api/invoices", json=_invoice_payload(practice2["id"], invoice_number="FT-FILTER-2"))

    filtered = await authed_client.get("/api/invoices", params={"practice_id": practice1["id"]})
    assert filtered.status_code == 200
    numbers = {i["invoice_number"] for i in filtered.json()}
    assert numbers == {"FT-FILTER-1"}
