from sqlalchemy.ext.asyncio import AsyncSession

from domain.company_location.rules import ensure_name_valid
from domain.errors import NotFoundError
from models.company_location import CompanyLocation
from repositories.audit_repository import AuditRepository
from repositories.reference_repositories import CompanyLocationRepository
from schemas.company_location import CompanyLocationCreate, CompanyLocationUpdate

ENTITY_TYPE = "company_location"


async def create_location(db: AsyncSession, data: CompanyLocationCreate, *, actor_user_id: int) -> CompanyLocation:
    ensure_name_valid(data.name)

    repo = CompanyLocationRepository(db)
    audit = AuditRepository(db)

    location = CompanyLocation(**data.model_dump(), created_by=actor_user_id, updated_by=actor_user_id)
    repo.add(location)
    await db.flush()

    audit.record(entity_type=ENTITY_TYPE, entity_id=location.id, action="created", user_id=actor_user_id)

    await db.commit()
    await db.refresh(location)
    return location


async def update_location(
    db: AsyncSession, location_id: int, data: CompanyLocationUpdate, *, actor_user_id: int
) -> CompanyLocation:
    ensure_name_valid(data.name)

    repo = CompanyLocationRepository(db)
    audit = AuditRepository(db)

    location = await repo.get_by_id(location_id)
    if location is None:
        raise NotFoundError(f"Sede {location_id} non trovata")

    changes = data.model_dump()
    for field_name, new_value in changes.items():
        old_value = getattr(location, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=location.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(location, field_name, new_value)
    location.updated_by = actor_user_id

    await db.commit()
    await db.refresh(location)
    return location


async def deactivate_location(db: AsyncSession, location_id: int, *, actor_user_id: int) -> CompanyLocation:
    repo = CompanyLocationRepository(db)
    audit = AuditRepository(db)

    location = await repo.get_by_id(location_id)
    if location is None or not location.active:
        raise NotFoundError(f"Sede {location_id} non trovata")

    location.active = False
    location.updated_by = actor_user_id
    audit.record(entity_type=ENTITY_TYPE, entity_id=location.id, action="deactivated", user_id=actor_user_id)

    await db.commit()
    await db.refresh(location)
    return location
