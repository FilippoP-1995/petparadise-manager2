from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes import (
    articles,
    auth,
    clients,
    company_locations,
    cremation_cycles,
    deliveries,
    invoices,
    payments,
    pickups,
    practices,
    references,
    urns,
    veterinarians,
)

app = FastAPI(title="Pet Paradise Manager V2 API")

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(veterinarians.router)
app.include_router(practices.router)
app.include_router(references.router)
app.include_router(pickups.router)
app.include_router(deliveries.router)
app.include_router(cremation_cycles.router)
app.include_router(company_locations.router)
app.include_router(urns.router)
app.include_router(articles.router)
app.include_router(invoices.router)
app.include_router(payments.router)


@app.get("/health")
async def health():
    return {"ok": True}


# Serving della SPA React (frontend/dist) dalla stessa origin del backend -
# il client frontend usa baseUrl="" (vedi frontend/src/shared/api/client.ts),
# quindi presuppone che /api/* e la SPA vivano sotto lo stesso host:porta.
# Attivo solo se frontend/dist esiste davvero: in sviluppo locale con Vite
# (npm run dev) il frontend viene servito separatamente da Vite stesso e
# questa cartella puo' non esistere - il backend deve continuare a partire
# comunque, offrendo solo /api e /health (comportamento locale invariato).
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
    if (FRONTEND_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str) -> FileResponse:
        # Registrata per ultima, dopo tutti gli include_router() sopra:
        # FastAPI prova le route nell'ordine di registrazione, quindi
        # /api/*, /health e /assets/* restano sempre risolte dalle route
        # specifiche prima di arrivare qui. Il controllo esplicito sotto
        # e' una seconda barriera, non l'unica: un path sotto /api, /assets
        # o /health che non corrisponde a nessuna route reale deve restare
        # un 404 vero, non essere mascherato dalla SPA (altrimenti un
        # errore di routing lato client diventerebbe invisibile).
        if full_path.startswith("api/") or full_path.startswith("assets/") or full_path == "health":
            raise HTTPException(status_code=404)
        return FileResponse(FRONTEND_DIST / "index.html")
