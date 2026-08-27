from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.cremation_cycle.rules import ensure_animal_eligible, ensure_capacity_available, ensure_not_locked_in_completed_cycle
from domain.cremation_cycle.state_machine import (
    derive_status_after_count_change,
    ensure_deletable,
    validate_completion,
    validate_revert,
)
from domain.errors import NotFoundError, ValidationDomainError
from models.animal import Animal
from models.cremation_cycle import CremationCycle, CremationCycleStatus
from models.practice import Practice, PracticeStatus
from repositories.audit_repository import AuditRepository
from repositories.cremation_cycle_repository import AnimalCycleRepository, CremationCycleRepository
from repositories.practice_repository import PracticeRepository
from schemas.cremation_cycle import CremationCycleCreate, CremationCycleUpdate
from services import practice_service

ENTITY_TYPE = "cremation_cycle"
ANIMAL_ENTITY_TYPE = "animal"

_MUTABLE_FIELDS = ("cycle_date", "planned_start", "planned_end", "cremation_location_id")


async def create_cycle(db: AsyncSession, data: CremationCycleCreate, *, actor_user_id: int) -> CremationCycle:
    repo = CremationCycleRepository(db)
    cycle = CremationCycle(
        status=CremationCycleStatus.pianificato,  # doc14 §4: creazione, 0 animali -> sempre pianificato
        created_by=actor_user_id,
    )
    for field_name in _MUTABLE_FIELDS:
        setattr(cycle, field_name, getattr(data, field_name))
    repo.add(cycle)
    await db.flush()
    AuditRepository(db).record(entity_type=ENTITY_TYPE, entity_id=cycle.id, action="created", user_id=actor_user_id)

    await db.commit()
    return await repo.get_by_id(cycle.id)


async def update_cycle(db: AsyncSession, cycle_id: int, data: CremationCycleUpdate, *, actor_user_id: int) -> CremationCycle:
    repo = CremationCycleRepository(db)
    cycle = await repo.get_by_id(cycle_id)
    if cycle is None:
        raise NotFoundError(f"Ciclo {cycle_id} non trovato")
    if cycle.status == CremationCycleStatus.completato:
        raise ValidationDomainError("Un ciclo completato e' un record storico: la programmazione non e' piu' modificabile.")

    audit = AuditRepository(db)
    for field_name in _MUTABLE_FIELDS:
        old_value = getattr(cycle, field_name)
        new_value = getattr(data, field_name)
        if old_value != new_value:
            audit.record(
                entity_type=ENTITY_TYPE,
                entity_id=cycle.id,
                action="field_changed",
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                user_id=actor_user_id,
            )
        setattr(cycle, field_name, new_value)

    await db.commit()
    return await repo.get_by_id(cycle_id)


async def delete_cycle(db: AsyncSession, cycle_id: int, *, actor_user_id: int) -> None:
    """Release hardening: lock di riga prima della verifica (stesso
    principio gia' usato per assign_animal/remove_animal sullo stesso
    ciclo) - senza, un'assegnazione animale concorrente potrebbe
    intrufolarsi tra il controllo '0 animali' e la cancellazione."""
    repo = CremationCycleRepository(db)
    cycle = await repo.get_by_id_for_update(cycle_id)
    if cycle is None:
        raise NotFoundError(f"Ciclo {cycle_id} non trovato")
    ensure_deletable(cycle.status, len(cycle.animals))

    AuditRepository(db).record(entity_type=ENTITY_TYPE, entity_id=cycle.id, action="deleted", user_id=actor_user_id)
    await db.delete(cycle)
    await db.commit()


async def assign_animal(db: AsyncSession, cycle_id: int, animal_id: int, *, actor_user_id: int) -> CremationCycle:
    """Assegna (o riassegna, se l'animale era gia' su un altro ciclo non
    completato) un animale al ciclo - lock di riga su animale e cicli
    coinvolti per tutta la transazione, stesso principio gia' usato e
    testato per Ritiro->Pratica."""
    animal_repo = AnimalCycleRepository(db)
    cycle_repo = CremationCycleRepository(db)
    audit = AuditRepository(db)

    animal = await animal_repo.get_for_update(animal_id)
    if animal is None:
        raise NotFoundError(f"Animale {animal_id} non trovato")
    if animal.practice_id is None:
        raise ValidationDomainError("L'animale deve appartenere a una pratica prima di essere assegnato a un ciclo.")

    practice = await PracticeRepository(db).get_by_id(animal.practice_id)
    if practice is None:
        raise NotFoundError(f"Pratica {animal.practice_id} non trovata")
    ensure_animal_eligible(practice_service_type=practice.service_type, practice_status=practice.status.value)

    new_cycle = await cycle_repo.get_by_id_for_update(cycle_id)
    if new_cycle is None:
        raise NotFoundError(f"Ciclo {cycle_id} non trovato")
    if new_cycle.status == CremationCycleStatus.completato:
        raise ValidationDomainError("Non e' possibile assegnare un animale a un ciclo gia' completato.")

    old_cycle_id = animal.cremation_cycle_id
    if old_cycle_id == cycle_id:
        raise ValidationDomainError("L'animale e' gia' assegnato a questo ciclo.")

    old_cycle = None
    if old_cycle_id is not None:
        old_cycle = await cycle_repo.get_by_id_for_update(old_cycle_id)
        if old_cycle is not None:
            ensure_not_locked_in_completed_cycle(old_cycle.status.value)

    ensure_capacity_available(len(new_cycle.animals))

    # doc: come per Ritiro->Pratica, un animale va spostato attraverso le
    # collezioni ORM (non assegnando il FK direttamente) - altrimenti la
    # lista `animals` gia' caricata in sessione (selectinload) resta
    # disallineata dal valore appena scritto, sia per i controlli di
    # capacita' successivi sia per l'oggetto restituito al chiamante.
    if old_cycle is not None:
        old_cycle.animals.remove(animal)
    new_cycle.animals.append(animal)

    audit.record(
        entity_type=ANIMAL_ENTITY_TYPE,
        entity_id=animal.id,
        action="assigned_to_cycle",
        old_value=str(old_cycle_id) if old_cycle_id is not None else None,
        new_value=str(new_cycle.id),
        user_id=actor_user_id,
    )

    if old_cycle is not None:
        old_cycle.status = derive_status_after_count_change(old_cycle.status, len(old_cycle.animals))

    new_cycle.status = derive_status_after_count_change(new_cycle.status, len(new_cycle.animals))

    await db.commit()
    return await cycle_repo.get_by_id(new_cycle.id)


async def remove_animal(db: AsyncSession, cycle_id: int, animal_id: int, *, actor_user_id: int) -> CremationCycle:
    animal_repo = AnimalCycleRepository(db)
    cycle_repo = CremationCycleRepository(db)

    animal = await animal_repo.get_for_update(animal_id)
    if animal is None or animal.cremation_cycle_id != cycle_id:
        raise NotFoundError(f"Animale {animal_id} non assegnato al ciclo {cycle_id}")

    cycle = await cycle_repo.get_by_id_for_update(cycle_id)
    if cycle is None:
        raise NotFoundError(f"Ciclo {cycle_id} non trovato")
    ensure_not_locked_in_completed_cycle(cycle.status.value)

    cycle.animals.remove(animal)
    AuditRepository(db).record(
        entity_type=ANIMAL_ENTITY_TYPE,
        entity_id=animal.id,
        action="removed_from_cycle",
        old_value=str(cycle_id),
        user_id=actor_user_id,
    )

    cycle.status = derive_status_after_count_change(cycle.status, len(cycle.animals))

    await db.commit()
    return await cycle_repo.get_by_id(cycle_id)


async def _all_animals_of_practice_are_cremated(db: AsyncSession, practice_id: int) -> bool:
    animals = (await db.execute(select(Animal).where(Animal.practice_id == practice_id))).scalars().all()
    if not animals:
        return False
    for animal in animals:
        if animal.cremation_cycle_id is None:
            return False
        cycle = await db.get(CremationCycle, animal.cremation_cycle_id)
        if cycle is None or cycle.status != CremationCycleStatus.completato:
            return False
    return True


async def complete_cycle(db: AsyncSession, cycle_id: int, *, actor_user_id: int) -> CremationCycle:
    """doc14 §1 + §4: completamento esplicito, side-effect automatico sulle
    pratiche i cui animali sono TUTTI ora in cicli completati (mai solo
    'questo ciclo e' completo' per una pratica con animali divisi su piu'
    cicli) - una sola transazione, un solo commit."""
    cycle_repo = CremationCycleRepository(db)
    cycle = await cycle_repo.get_by_id_for_update(cycle_id)
    if cycle is None:
        raise NotFoundError(f"Ciclo {cycle_id} non trovato")
    validate_completion(cycle.status)

    cycle.status = CremationCycleStatus.completato
    cycle.completed_at = datetime.now(timezone.utc)
    AuditRepository(db).record(entity_type=ENTITY_TYPE, entity_id=cycle.id, action="completed", user_id=actor_user_id)

    practice_repo = PracticeRepository(db)
    touched_practice_ids = {a.practice_id for a in cycle.animals if a.practice_id is not None}
    for practice_id in touched_practice_ids:
        practice = await practice_repo.get_by_id(practice_id)
        # Difensivo (come V1, cremation_complete_cycle): se la pratica non e'
        # nello stato atteso, non forzarla - non bloccare pero' le altre.
        if practice is None or practice.status != PracticeStatus.in_programma:
            continue
        if await _all_animals_of_practice_are_cremated(db, practice_id):
            await practice_service.apply_automatic_cycle_side_effect(
                db,
                practice,
                PracticeStatus.cremato,
                cremation_registered=True,
                cycle_id=cycle.id,
                actor_user_id=actor_user_id,
            )

    await db.commit()
    return await cycle_repo.get_by_id(cycle_id)


async def revert_cycle(db: AsyncSession, cycle_id: int, reason: str, *, actor_user_id: int) -> CremationCycle:
    """Percorso di correzione (doc15 decisione #10, gia' chiuso): non una
    procedura nuova, lo stesso ripristino completato->in_attesa gia'
    previsto - motivo obbligatorio perche' tocca un record storico."""
    cycle_repo = CremationCycleRepository(db)
    cycle = await cycle_repo.get_by_id_for_update(cycle_id)
    if cycle is None:
        raise NotFoundError(f"Ciclo {cycle_id} non trovato")
    validate_revert(cycle.status)

    cycle.status = CremationCycleStatus.in_attesa
    cycle.completed_at = None
    AuditRepository(db).record(
        entity_type=ENTITY_TYPE, entity_id=cycle.id, action="reverted", user_id=actor_user_id, reason=reason
    )

    practice_repo = PracticeRepository(db)
    touched_practice_ids = {a.practice_id for a in cycle.animals if a.practice_id is not None}
    for practice_id in touched_practice_ids:
        practice = await practice_repo.get_by_id(practice_id)
        if practice is None or practice.status != PracticeStatus.cremato:
            continue
        await practice_service.apply_automatic_cycle_side_effect(
            db,
            practice,
            PracticeStatus.in_programma,
            cremation_registered=False,
            cycle_id=cycle.id,
            actor_user_id=actor_user_id,
        )

    await db.commit()
    return await cycle_repo.get_by_id(cycle_id)
