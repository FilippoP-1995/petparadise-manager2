from sqlalchemy.ext.asyncio import AsyncSession

from domain.errors import NotFoundError
from domain.veterinarian.rules import HoursInput, ensure_identifiable, ensure_valid_hours
from models.veterinarian import Veterinarian, VeterinarianHours
from repositories.audit_repository import AuditRepository
from repositories.veterinarian_repository import VeterinarianRepository
from schemas.veterinarian import VeterinarianCreate, VeterinarianUpdate

ENTITY_TYPE = "veterinarian"


def _to_hours_inputs(hours) -> list[HoursInput]:
    return [
        HoursInput(
            day_of_week=h.day_of_week,
            closed=h.closed,
            morning_start=h.morning_start.isoformat() if h.morning_start else None,
            morning_end=h.morning_end.isoformat() if h.morning_end else None,
            afternoon_start=h.afternoon_start.isoformat() if h.afternoon_start else None,
            afternoon_end=h.afternoon_end.isoformat() if h.afternoon_end else None,
            notes=h.notes,
        )
        for h in hours
    ]


async def create_veterinarian(db: AsyncSession, data: VeterinarianCreate, *, actor_user_id: int) -> Veterinarian:
    ensure_identifiable(data.clinic_name, data.doctor_name)
    ensure_valid_hours(_to_hours_inputs(data.hours))

    repo = VeterinarianRepository(db)
    audit = AuditRepository(db)

    fields = data.model_dump(exclude={"hours"})
    veterinarian = Veterinarian(**fields, created_by=actor_user_id, updated_by=actor_user_id)
    veterinarian.hours = [VeterinarianHours(**h.model_dump()) for h in data.hours]
    repo.add(veterinarian)
    await db.flush()

    audit.record(entity_type=ENTITY_TYPE, entity_id=veterinarian.id, action="created", user_id=actor_user_id)

    await db.commit()
    await db.refresh(veterinarian, attribute_names=["hours"])
    return veterinarian


async def update_veterinarian(
    db: AsyncSession, veterinarian_id: int, data: VeterinarianUpdate, *, actor_user_id: int
) -> Veterinarian:
    ensure_identifiable(data.clinic_name, data.doctor_name)
    ensure_valid_hours(_to_hours_inputs(data.hours))

    repo = VeterinarianRepository(db)
    audit = AuditRepository(db)

    veterinarian = await repo.get_by_id(veterinarian_id)
    if veterinarian is None or not veterinarian.active:
        raise NotFoundError(f"Veterinario {veterinarian_id} non trovato")

    fields = data.model_dump(exclude={"hours"})
    for field_name, new_value in fields.items():
        old_value = getattr(veterinarian, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=veterinarian.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(veterinarian, field_name, new_value)
    veterinarian.updated_by = actor_user_id

    # Sostituzione completa degli orari (tabella di dettaglio, hard delete
    # reale gia' previsto dal modello - doc06 "Soft delete").
    veterinarian.hours = [VeterinarianHours(**h.model_dump()) for h in data.hours]

    await db.commit()
    await db.refresh(veterinarian, attribute_names=["hours"])
    return veterinarian


async def deactivate_veterinarian(db: AsyncSession, veterinarian_id: int, *, actor_user_id: int) -> Veterinarian:
    repo = VeterinarianRepository(db)
    audit = AuditRepository(db)

    veterinarian = await repo.get_by_id(veterinarian_id)
    if veterinarian is None or not veterinarian.active:
        raise NotFoundError(f"Veterinario {veterinarian_id} non trovato")

    veterinarian.active = False
    veterinarian.updated_by = actor_user_id
    audit.record(entity_type=ENTITY_TYPE, entity_id=veterinarian.id, action="deactivated", user_id=actor_user_id)

    await db.commit()
    await db.refresh(veterinarian, attribute_names=["hours"])
    return veterinarian
