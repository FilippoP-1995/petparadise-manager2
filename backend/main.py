from fastapi import FastAPI

from api.routes import auth, clients, cremation_cycles, deliveries, pickups, practices, references, veterinarians

app = FastAPI(title="Pet Paradise Manager V2 API")

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(veterinarians.router)
app.include_router(practices.router)
app.include_router(references.router)
app.include_router(pickups.router)
app.include_router(deliveries.router)
app.include_router(cremation_cycles.router)


@app.get("/health")
async def health():
    return {"ok": True}
