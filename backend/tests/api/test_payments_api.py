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


def _payment_payload(practice_id=None, **overrides):
    base = {
        "practice_id": practice_id, "movement_date": "2026-01-01", "channel": "W",
        "ledger_section": "Entrata", "movement_type": "Acconto", "amount_cents": 12000,
    }
    base.update(overrides)
    return base


async def test_list_payments_requires_authentication(client: AsyncClient):
    response = await client.get("/api/payments", params={"practice_id": 1})
    assert response.status_code == 401


async def test_register_payment_returns_201(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    response = await authed_client.post("/api/payments", json=_payment_payload(practice["id"]))
    assert response.status_code == 201
    body = response.json()
    assert body["amount_cents"] == 12000
    assert body["practice_number_snapshot"] == practice["practice_number"]


async def test_register_payment_rejects_zero_amount_with_422(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    response = await authed_client.post("/api/payments", json=_payment_payload(practice["id"], amount_cents=0))
    assert response.status_code == 422


async def test_operator_can_register_and_reverse_payment(
    client: AsyncClient, operator_user: User, admin_user: User, sample_client, sample_location
):
    """FACT V1: nessuna restrizione di ruolo su registrazione/storno pagamenti."""

    async def _as_admin():
        return admin_user

    app.dependency_overrides[get_current_user] = _as_admin
    practice = await _create_practice(client, sample_client, sample_location)

    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        create = await client.post("/api/payments", json=_payment_payload(practice["id"]))
        assert create.status_code == 201
        payment_id = create.json()["id"]

        reverse = await client.post(f"/api/payments/{payment_id}/storna", json={"reason": "errore operatore"})
        assert reverse.status_code == 200
        assert reverse.json()["movement_type"] == "Storno"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_reverse_payment_twice_returns_422(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    payment = (await authed_client.post("/api/payments", json=_payment_payload(practice["id"]))).json()

    first = await authed_client.post(f"/api/payments/{payment['id']}/storna", json={"reason": "primo storno"})
    assert first.status_code == 200
    second = await authed_client.post(f"/api/payments/{payment['id']}/storna", json={"reason": "secondo tentativo"})
    assert second.status_code == 422


async def test_delete_and_restore_payment(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    payment = (await authed_client.post("/api/payments", json=_payment_payload(practice["id"]))).json()

    deletion = await authed_client.post(
        f"/api/payments/{payment['id']}/elimina", json={"deletion_kind": "errore_inserimento", "reason": "riga duplicata"}
    )
    assert deletion.status_code == 200
    deletion_body = deletion.json()
    assert deletion_body["payment_id"] == payment["id"]

    missing = await authed_client.get(f"/api/payments/{payment['id']}")
    assert missing.status_code == 404

    restore = await authed_client.post(f"/api/payments/deletions/{deletion_body['id']}/ripristina")
    assert restore.status_code == 200
    assert restore.json()["id"] == payment["id"]

    restored_get = await authed_client.get(f"/api/payments/{payment['id']}")
    assert restored_get.status_code == 200


async def test_delete_payment_requires_reason_with_422(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    payment = (await authed_client.post("/api/payments", json=_payment_payload(practice["id"]))).json()

    response = await authed_client.post(f"/api/payments/{payment['id']}/elimina", json={"deletion_kind": "errore_inserimento", "reason": ""})
    assert response.status_code == 422


async def test_practice_reconciliation_endpoint(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    await authed_client.post("/api/payments", json=_payment_payload(practice["id"], amount_cents=34000, movement_type="Incasso completo"))

    recon = await authed_client.get(f"/api/payments/practice/{practice['id']}/riconciliazione")
    assert recon.status_code == 200
    body = recon.json()
    assert body["effective_total_cents"] == 34000
    assert body["paid_total_cents"] == 34000
    assert body["status"] == "pagata"


async def test_list_payments_for_practice(authed_client: AsyncClient, sample_client, sample_location):
    practice = await _create_practice(authed_client, sample_client, sample_location)
    await authed_client.post("/api/payments", json=_payment_payload(practice["id"], amount_cents=5000))
    await authed_client.post("/api/payments", json=_payment_payload(practice["id"], amount_cents=7000, movement_type="Saldo"))

    response = await authed_client.get("/api/payments", params={"practice_id": practice["id"]})
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_unknown_payment_returns_404(authed_client: AsyncClient):
    response = await authed_client.get("/api/payments/999999")
    assert response.status_code == 404


async def test_operator_cannot_delete_payment(
    client: AsyncClient, operator_user: User, admin_user: User, sample_client, sample_location
):
    """Release hardening: cancellazione fisica di un pagamento - solo Admin."""

    async def _as_admin():
        return admin_user

    app.dependency_overrides[get_current_user] = _as_admin
    practice = await _create_practice(client, sample_client, sample_location)
    payment = (await client.post("/api/payments", json=_payment_payload(practice["id"]))).json()

    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        response = await client.post(
            f"/api/payments/{payment['id']}/elimina", json={"deletion_kind": "errore_inserimento", "reason": "test"}
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_admin_can_delete_payment(client: AsyncClient, admin_user: User, sample_client, sample_location):
    async def _as_admin():
        return admin_user

    app.dependency_overrides[get_current_user] = _as_admin
    try:
        practice = await _create_practice(client, sample_client, sample_location)
        payment = (await client.post("/api/payments", json=_payment_payload(practice["id"]))).json()

        response = await client.post(
            f"/api/payments/{payment['id']}/elimina", json={"deletion_kind": "errore_inserimento", "reason": "test"}
        )
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_operator_cannot_restore_payment_deletion(
    client: AsyncClient, operator_user: User, admin_user: User, sample_client, sample_location
):
    """Release hardening: ripristino di un pagamento cancellato - solo Admin."""

    async def _as_admin():
        return admin_user

    app.dependency_overrides[get_current_user] = _as_admin
    practice = await _create_practice(client, sample_client, sample_location)
    payment = (await client.post("/api/payments", json=_payment_payload(practice["id"]))).json()
    deletion = (
        await client.post(f"/api/payments/{payment['id']}/elimina", json={"deletion_kind": "errore_inserimento", "reason": "test"})
    ).json()

    async def _as_operator():
        return operator_user

    app.dependency_overrides[get_current_user] = _as_operator
    try:
        response = await client.post(f"/api/payments/deletions/{deletion['id']}/ripristina")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


async def test_admin_can_restore_payment_deletion(client: AsyncClient, admin_user: User, sample_client, sample_location):
    async def _as_admin():
        return admin_user

    app.dependency_overrides[get_current_user] = _as_admin
    try:
        practice = await _create_practice(client, sample_client, sample_location)
        payment = (await client.post("/api/payments", json=_payment_payload(practice["id"]))).json()
        deletion = (
            await client.post(
                f"/api/payments/{payment['id']}/elimina", json={"deletion_kind": "errore_inserimento", "reason": "test"}
            )
        ).json()

        response = await client.post(f"/api/payments/deletions/{deletion['id']}/ripristina")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
