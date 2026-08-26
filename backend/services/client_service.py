from sqlalchemy.ext.asyncio import AsyncSession

from domain.client.rules import ensure_identifiable
from domain.errors import NotFoundError
from models.client import Client
from repositories.audit_repository import AuditRepository
from repositories.client_repository import ClientRepository
from schemas.client import ClientCreate, ClientUpdate

ENTITY_TYPE = "client"


async def create_client(db: AsyncSession, data: ClientCreate, *, actor_user_id: int) -> Client:
    """Caso d'uso completo: valida -> scrive -> registra audit, tutto nella
    stessa transazione (doc09 'Regola vincolante - atomicita' modifica di
    dominio + audit')."""
    ensure_identifiable(data.first_name, data.last_name, data.company_name)

    repo = ClientRepository(db)
    audit = AuditRepository(db)

    client = Client(**data.model_dump(), created_by=actor_user_id, updated_by=actor_user_id)
    repo.add(client)
    await db.flush()  # assegna client.id senza chiudere la transazione

    audit.record(entity_type=ENTITY_TYPE, entity_id=client.id, action="created", user_id=actor_user_id)

    await db.commit()
    await db.refresh(client)
    return client


async def update_client(db: AsyncSession, client_id: int, data: ClientUpdate, *, actor_user_id: int) -> Client:
    ensure_identifiable(data.first_name, data.last_name, data.company_name)

    repo = ClientRepository(db)
    audit = AuditRepository(db)

    client = await repo.get_by_id(client_id)
    if client is None or not client.active:
        raise NotFoundError(f"Cliente {client_id} non trovato")

    changes = data.model_dump()
    for field_name, new_value in changes.items():
        old_value = getattr(client, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=client.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(client, field_name, new_value)
    client.updated_by = actor_user_id

    await db.commit()
    await db.refresh(client)
    return client


async def deactivate_client(db: AsyncSession, client_id: int, *, actor_user_id: int) -> Client:
    """Solo Admin (vedi api/routes/clients.py) - non e' una DELETE, e'
    un flag 'active' come gia' oggi in V1 per questa entita'."""
    repo = ClientRepository(db)
    audit = AuditRepository(db)

    client = await repo.get_by_id(client_id)
    if client is None or not client.active:
        raise NotFoundError(f"Cliente {client_id} non trovato")

    client.active = False
    client.updated_by = actor_user_id
    audit.record(entity_type=ENTITY_TYPE, entity_id=client.id, action="deactivated", user_id=actor_user_id)

    await db.commit()
    await db.refresh(client)
    return client
