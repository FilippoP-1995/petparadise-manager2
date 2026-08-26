from httpx import AsyncClient


async def test_list_articles_requires_authentication(client: AsyncClient):
    response = await client.get("/api/articles")
    assert response.status_code == 401


async def test_list_articles_includes_seeded_defaults(authed_client: AsyncClient):
    """doc07/migrazione: stesso set fisso di 6 nomi gia' seedato da V1."""
    response = await authed_client.get("/api/articles")
    assert response.status_code == 200
    names = {a["name"] for a in response.json()}
    assert "Sacchi per ritiro" in names
    assert "Cerniere e viti urne" in names


async def test_order_article_creates_request_and_appears_in_recent(authed_client: AsyncClient):
    articles = await authed_client.get("/api/articles")
    article_id = articles.json()[0]["id"]

    response = await authed_client.post(f"/api/articles/{article_id}/ordina")
    assert response.status_code == 201
    assert response.json()["article_id"] == article_id

    recent = await authed_client.get("/api/articles/orders/recent")
    assert recent.status_code == 200
    assert any(o["article_id"] == article_id for o in recent.json())


async def test_order_unknown_article_returns_404(authed_client: AsyncClient):
    response = await authed_client.post("/api/articles/999999/ordina")
    assert response.status_code == 404
