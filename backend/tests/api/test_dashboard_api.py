async def test_dashboard_summary_requires_authentication(client):
    response = await client.get("/api/dashboard/summary")
    assert response.status_code == 401


async def test_dashboard_summary_returns_200(authed_client):
    response = await authed_client.get("/api/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert "practice_status_counts" in body
    assert "payment_status_counts" in body
    assert "daily_incassi" in body
    assert len(body["daily_incassi"]) == 7


async def test_dashboard_summary_rejects_invalid_period(authed_client):
    response = await authed_client.get("/api/dashboard/summary", params={"period": "anno"})
    assert response.status_code == 422
