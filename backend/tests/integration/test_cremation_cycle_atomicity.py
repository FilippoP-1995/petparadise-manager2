"""Test di atomicita' specifici del dominio Cicli di Cremazione (doc09:
ogni scrittura di dominio + il proprio audit_log nella stessa transazione).
Non una copia dei test di atomicita' degli altri domini: qui il caso
rilevante e' 'complete_cycle' che tocca PIU' pratiche in una sola
transazione - se il commit fallisce, nessuna delle pratiche coinvolte deve
risultare parzialmente cremata. Stessa tecnica di iniezione di fallimento
gia' usata altrove (vincolo NOT NULL reale su entity_type)."""

from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models.audit_log import AuditLog
from models.cremation_cycle import CremationCycleStatus
from models.practice import PracticeStatus
from repositories.audit_repository import AuditRepository
from repositories.cremation_cycle_repository import AnimalCycleRepository, CremationCycleRepository
from repositories.practice_repository import PracticeRepository
from schemas.cremation_cycle import CremationCycleCreate
from schemas.practice import AnimalInput, PracticeCreate, TransitionRequest
from services import cremation_cycle_service, practice_service


def _cycle_data(**overrides):
    base = dict(cycle_date=date(2026, 9, 10), planned_start=time(9, 0), planned_end=time(10, 30))
    base.update(overrides)
    return CremationCycleCreate(**base)


async def _eligible_practice(db_session, admin_user, sample_client, sample_location, names):
    practice = await practice_service.create_practice(
        db_session,
        PracticeCreate(
            client_id=sample_client.id,
            destination_branch_id=sample_location.id,
            request_origin="Collaboratore",
            service_type="Cremazione singola",
            animals=[AnimalInput(name=n) for n in names],
        ),
        actor_user_id=admin_user.id,
    )
    return await practice_service.transition_practice_state(
        db_session, practice.id, TransitionRequest(target_status=PracticeStatus.in_programma), actor_user_id=admin_user.id
    )


async def test_failed_audit_rolls_back_animal_assignment(db_session, admin_user, sample_client, sample_location):
    """Replica dei passi di assign_animal, fallimento forzato nell'ultimo
    passo - l'animale non deve risultare assegnato ne' il ciclo cambiato di
    stato se la transazione fallisce."""
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice = await _eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    animal_id = practice.animals[0].id
    cycle_id = cycle.id  # catturato PRIMA del rollback: dopo il rollback l'oggetto ORM e' expired

    cycle_repo = CremationCycleRepository(db_session)
    animal_repo = AnimalCycleRepository(db_session)

    reloaded_cycle = await cycle_repo.get_by_id_for_update(cycle_id)
    animal = await animal_repo.get_for_update(animal_id)
    reloaded_cycle.animals.append(animal)
    reloaded_cycle.status = CremationCycleStatus.in_attesa

    AuditRepository(db_session).record(
        entity_type=None, entity_id=animal_id, action="assigned_to_cycle", user_id=admin_user.id
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final_cycle = await cycle_repo.get_by_id(cycle_id)
    assert final_cycle.status == CremationCycleStatus.pianificato, "il ciclo non deve cambiare stato se l'assegnazione fallisce"
    assert final_cycle.animals == [], "l'animale non deve risultare assegnato se la transazione fallisce"


async def test_failed_completion_leaves_no_partial_practice_transitions(
    db_session, admin_user, sample_client, sample_location
):
    """Lo scenario esplicitamente richiesto: complete_cycle tocca DUE
    pratiche distinte nella stessa transazione. Se il commit fallisce a
    meta', nessuna delle due deve risultare cremata, il ciclo deve restare
    non completato, e non deve restare traccia di audit orfana per
    l'azione 'completed'."""
    cycle = await cremation_cycle_service.create_cycle(db_session, _cycle_data(), actor_user_id=admin_user.id)
    practice_a = await _eligible_practice(db_session, admin_user, sample_client, sample_location, ["Fido"])
    practice_b = await _eligible_practice(db_session, admin_user, sample_client, sample_location, ["Micio"])
    cycle_id = cycle.id  # catturati PRIMA del rollback: dopo il rollback gli oggetti ORM sono expired
    practice_a_id = practice_a.id
    practice_b_id = practice_b.id

    await cremation_cycle_service.assign_animal(db_session, cycle_id, practice_a.animals[0].id, actor_user_id=admin_user.id)
    await cremation_cycle_service.assign_animal(db_session, cycle_id, practice_b.animals[0].id, actor_user_id=admin_user.id)

    cycle_repo = CremationCycleRepository(db_session)
    practice_repo = PracticeRepository(db_session)

    # Replica manuale dei passi di complete_cycle, per poter iniettare il
    # fallimento DOPO che entrambe le pratiche sono gia' state toccate in
    # memoria (il punto piu' critico per verificare l'atomicita').
    reloaded_cycle = await cycle_repo.get_by_id_for_update(cycle_id)
    reloaded_cycle.status = CremationCycleStatus.completato
    reloaded_cycle.completed_at = datetime.now(timezone.utc)
    AuditRepository(db_session).record(
        entity_type="cremation_cycle", entity_id=cycle_id, action="completed", user_id=admin_user.id
    )

    pa = await practice_repo.get_by_id(practice_a_id)
    pb = await practice_repo.get_by_id(practice_b_id)
    await practice_service.apply_automatic_cycle_side_effect(
        db_session, pa, PracticeStatus.cremato, cremation_registered=True, cycle_id=cycle_id, actor_user_id=admin_user.id
    )
    await practice_service.apply_automatic_cycle_side_effect(
        db_session, pb, PracticeStatus.cremato, cremation_registered=True, cycle_id=cycle_id, actor_user_id=admin_user.id
    )

    # Fallimento forzato nell'ultimo passo - stessa tecnica (NOT NULL) gia'
    # usata per Clienti/Veterinari/Pratiche/Ritiri.
    AuditRepository(db_session).record(entity_type=None, entity_id=cycle_id, action="forced_failure", user_id=admin_user.id)

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    final_cycle = await cycle_repo.get_by_id(cycle_id)
    assert final_cycle.status == CremationCycleStatus.in_attesa, "il ciclo non deve risultare completato se la transazione fallisce"
    assert final_cycle.completed_at is None

    final_a = await practice_repo.get_by_id(practice_a_id)
    final_b = await practice_repo.get_by_id(practice_b_id)
    assert final_a.status == PracticeStatus.in_programma, "nessuna pratica deve risultare cremata a meta' transazione"
    assert final_b.status == PracticeStatus.in_programma, "nessuna pratica deve risultare cremata a meta' transazione"

    orphan_rows = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "completed", AuditLog.entity_id == cycle_id))
    ).scalars().all()
    assert orphan_rows == [], "nessuna riga di audit orfana deve sopravvivere al rollback"
