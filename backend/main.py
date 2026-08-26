from fastapi import FastAPI

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
