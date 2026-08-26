from fastapi import FastAPI

from api.routes import auth, clients, veterinarians

app = FastAPI(title="Pet Paradise Manager V2 API")

app.include_router(auth.router)
app.include_router(clients.router)
app.include_router(veterinarians.router)


@app.get("/health")
async def health():
    return {"ok": True}
