"""Verifica il serving della SPA React (frontend/dist) dalla stessa
origin del backend, e che non collida con /api/*, /health, /assets/*.
Richiede una build reale del frontend (frontend/dist) - salta questi test
se non presente, invece di fallire un checkout/CI che non ha ancora
eseguito `npm run build` (il codice applicativo resta comunque testato
altrove: qui si verifica solo l'integrazione col frontend gia' buildato)."""

import pytest

from main import FRONTEND_DIST

pytestmark = pytest.mark.skipif(
    not (FRONTEND_DIST / "index.html").is_file(),
    reason="frontend/dist non presente - eseguire 'npm run build' in frontend/ prima di questi test",
)


async def test_root_serves_the_spa_index_html(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "<div id=\"root\"></div>" in response.text


async def test_a_react_route_is_served_via_spa_fallback(client):
    # /pratiche/123 non e' una route del backend: deve restituire lo
    # stesso index.html (il routing reale avviene lato client, in React
    # Router), non un 404 - altrimenti un refresh diretto su una pagina
    # interna dell'app romperebbe l'applicazione.
    response = await client.get("/pratiche/123")
    assert response.status_code == 200
    assert "<div id=\"root\"></div>" in response.text


async def test_a_nested_react_route_is_also_served_via_spa_fallback(client):
    response = await client.get("/cicli-cremazione/1")
    assert response.status_code == 200
    assert "<div id=\"root\"></div>" in response.text


async def test_a_real_built_asset_is_served(client):
    asset_files = list((FRONTEND_DIST / "assets").glob("*.js"))
    assert asset_files, "nessun asset .js trovato in frontend/dist/assets"

    response = await client.get(f"/assets/{asset_files[0].name}")
    assert response.status_code == 200


async def test_spa_fallback_does_not_intercept_unmatched_api_paths(client):
    # Un path sotto /api/ che non corrisponde a nessuna route reale deve
    # restare un 404 vero (probabile errore di routing lato client), non
    # essere mascherato silenziosamente dalla SPA.
    response = await client.get("/api/questo-path-non-esiste")
    assert response.status_code == 404
    assert "<div id=\"root\"></div>" not in response.text


async def test_health_endpoint_still_works_with_spa_fallback_active(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_auth_me_without_session_still_returns_401_not_spa_html(client):
    # Una vera route API (non trovata/non autorizzata) non deve mai
    # restituire l'HTML della SPA - conferma che l'ordine di registrazione
    # delle route (API prima, fallback SPA dopo) e' quello corretto.
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert "<div id=\"root\"></div>" not in response.text
